import asyncio
import csv
import dataclasses
import datetime
import io
import json
import logging
import zipfile
from enum import Enum, auto
from typing import Any, Literal, TypedDict

import aiohttp

from .env import (
    QUALTRICS_API_DATACENTER,
    QUALTRICS_API_TOKEN,
    QUALTRICS_FILTER_ID,
    QUALTRICS_SURVEY_ID,
)

logger = logging.getLogger(__name__)


class SurveyExportStartResult(TypedDict):
    progressId: str
    percentComplete: float
    status: Literal["inProgress", "complete", "failed"]
    continuationToken: str


class SurveyExportStartResponse(TypedDict):
    result: SurveyExportStartResult


class SurveyExportStatusResult(TypedDict):
    fileId: str
    percentComplete: float
    status: Literal["inProgress", "complete", "failed"]
    continuationToken: str


class SurveyExportStatusResponse(TypedDict):
    result: SurveyExportStatusResult


@dataclasses.dataclass()
class SurveyResponseAttachment:
    name: str
    filesize: int
    id: str
    mime_type: str


@dataclasses.dataclass()
class SurveyResponse:
    id: str
    name: str
    student_sys_id: str
    assignments: list[str]
    reason: str
    # Format date from a string to a datetime.date object
    date: datetime.date
    email: str | None
    file: SurveyResponseAttachment | None = None


class CompletionStatus(Enum):
    WAITING = auto()
    WAITING_FOR_PROF = auto()
    APPROVED = auto()
    DECLINED = auto()


class Qualtrics:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_token: str = QUALTRICS_API_TOKEN,
        datacenter: str = QUALTRICS_API_DATACENTER,
    ):
        self.api_token = api_token
        self.datacenter = datacenter
        self.session = session

    async def fetch(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        method: str = "get",
        content_type: str = "application/json",
    ) -> Any:
        """
        Fetch a URL.
        """
        func = getattr(self.session, method)
        url = self.api_url(endpoint)
        async with func(
            url,
            headers={"X-API-TOKEN": self.api_token, "Content-Type": "application/json"},
            params=params,
            data=json.dumps(data),
        ) as resp:
            resp.raise_for_status()
            if content_type == "application/json":
                return await resp.json()
            return await resp.read()

    def api_url(self, endpoint: str) -> str:
        return f"https://{self.datacenter}.qualtrics.com/API/v3/{endpoint}"

    async def start_survey_export(
        self,
        survey_id: str,
        filter_id: str,
    ) -> SurveyExportStartResponse:
        endpoint = f"surveys/{survey_id}/export-responses"
        data = {"format": "csv", "useLabels": True, "filterId": filter_id}
        return await self.fetch(endpoint, data=data, method="post")

    async def get_survey_export_progress(
        self,
        survey_id: str,
        progress_id: str,
    ) -> SurveyExportStatusResponse:
        endpoint = f"surveys/{survey_id}/export-responses/{progress_id}"
        return await self.fetch(endpoint)

    async def get_response(self, response_id: str) -> SurveyResponse:
        endpoint = f"surveys/{QUALTRICS_SURVEY_ID}/responses/{response_id}"
        resp = await self.fetch(endpoint)
        return SurveyResponse(
            id=response_id,
            name=resp["result"]["values"]["QID1_TEXT"],
            student_sys_id=resp["result"]["values"]["QID4_TEXT"],
            reason=resp["result"]["values"]["QID5_TEXT"],
            assignments=resp["result"]["labels"]["QID2"],
            file=(
                SurveyResponseAttachment(
                    name=resp["result"]["values"]["QID3_FILE_NAME"],
                    filesize=resp["result"]["values"]["QID3_FILE_SIZE"],
                    id=resp["result"]["values"]["QID3_FILE_ID"],
                    mime_type=resp["result"]["values"]["QID3_FILE_TYPE"],
                )
                if "QID3_FILE_NAME" in resp["result"]["values"]
                and resp["result"]["values"]["QID3_FILE_NAME"]
                else None
            ),
            email=resp.get("result", {}).get("values", {}).get("QID8_TEXT", None),
            date=datetime.datetime.strptime(
                resp["result"]["values"]["QID7_TEXT"],
                "%m-%d-%Y",
            ).date(),
        )

    async def update_completion_status(
        self,
        response_id: str,
        completion_status: CompletionStatus,
    ) -> None:
        # Update embedded data CompletionStatus field in the response to WAITING_FOR_PROF
        endpoint = f"responses/{response_id}"
        data = {
            "surveyId": QUALTRICS_SURVEY_ID,
            "embeddedData": {"CompletionStatus": completion_status.name},
        }
        return await self.fetch(endpoint, data=data, method="put")

    async def get_survey_export(
        self,
        survey_id: str,
        file_id: str,
    ) -> list[SurveyResponse]:
        endpoint = f"surveys/{survey_id}/export-responses/{file_id}/file"
        raw_zip_bytes: bytes = await self.fetch(
            endpoint,
            method="get",
            content_type="application/zip",
        )
        responses = []
        with zipfile.ZipFile(io.BytesIO(raw_zip_bytes)) as zip, zip.open(
            zip.namelist()[0],
        ) as csv_file:
            reader = csv.DictReader(io.TextIOWrapper(csv_file, encoding="utf-8"))
            # print all vals
            for i, row in enumerate(reader):
                if i < 2:
                    continue
                response = SurveyResponse(
                    id=row["ResponseId"],
                    name=row["Q1 "],
                    student_sys_id=row["Q2"],
                    assignments=[v for k, v in row.items() if k.startswith("Q3") and v],
                    reason=row["Q4"],
                    email=row.get("Q8"),
                    file=(
                        SurveyResponseAttachment(
                            name=row["Q5_Name"],
                            filesize=int(row["Q5_Size"]),
                            id=row["Q5_Id"],
                            mime_type=row["Q5_Type"],
                        )
                        if row["Q5_Name"]
                        else None
                    ),
                    date=datetime.datetime.strptime(row["Q6"], "%m-%d-%Y").date(),
                )
                responses.append(response)
        return responses

    async def get_responses(self) -> list[SurveyResponse]:
        logger.info("Fetching latest responses from Qualtrics...")
        survey_id, filter_id = QUALTRICS_SURVEY_ID, QUALTRICS_FILTER_ID
        resp = await self.start_survey_export(survey_id, filter_id)
        progress_id = resp["result"]["progressId"]
        while True:
            progress_resp = await self.get_survey_export_progress(
                survey_id,
                progress_id,
            )
            if progress_resp["result"]["status"] == "complete":
                logger.info("Qualtrics survey export complete.")
                return await self.get_survey_export(
                    survey_id,
                    progress_resp["result"]["fileId"],
                )
            await asyncio.sleep(1)
