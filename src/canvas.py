from __future__ import annotations

import datetime
import itertools
import logging
from typing import TYPE_CHECKING, Any, TypedDict

import aiohttp
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from .bot import CoordinateBot

logger = logging.getLogger(__name__)


class Course(TypedDict):
    id: int
    sis_course_id: str
    uuid: str
    integration_id: str
    sis_import_id: int
    name: str
    course_code: str
    original_name: str
    workflow_state: str
    account_id: int
    root_account_id: int
    enrollment_term_id: int
    # grading_periods: List[GradingPeriod]
    grading_standard_id: int
    grade_passback_setting: str
    created_at: str
    start_at: str
    end_at: str
    locale: str
    # enrollments: List[Enrollment]
    total_students: int
    # calendar: Calendar
    default_view: str
    syllabus_body: str
    needs_grading_count: int
    # term: Term
    # course_progress: CourseProgress
    apply_assignment_group_weights: bool
    # permissions: Permissions
    is_public: bool
    is_public_to_auth_users: bool
    public_syllabus: bool
    public_syllabus_to_auth: bool
    public_description: str
    storage_quota_mb: int
    storage_quota_used_mb: int
    hide_final_grades: bool
    license: str
    allow_student_assignment_edits: bool
    allow_wiki_comments: bool
    allow_student_forum_attachments: bool
    open_enrollment: bool
    self_enrollment: bool
    restrict_enrollments_to_course_dates: bool
    course_format: str
    access_restricted_by_date: bool
    time_zone: str
    blueprint: bool
    # blueprint_restrictions: BlueprintRestrictions
    # blueprint_restrictions_by_object_type: BlueprintRestrictionsByObjectType
    template: bool


class TurnitinSettings(TypedDict, total=False):
    originality_report_visibility: str
    exclude_small_matches_type: None | str
    exclude_small_matches_value: None | int | float


class NeedsGradingCountBySection(TypedDict):
    section_id: str
    needs_grading_count: int


class IntegrationData(TypedDict):
    key: str


class Assignment(TypedDict):
    id: int
    name: str
    description: str
    created_at: str
    updated_at: str
    due_at: str | None
    lock_at: str | None
    unlock_at: str | None
    has_overrides: bool
    all_dates: None
    course_id: int
    html_url: str
    submissions_download_url: str
    assignment_group_id: int
    due_date_required: bool
    allowed_extensions: list[str]
    max_name_length: int
    turnitin_enabled: bool
    vericite_enabled: bool
    turnitin_settings: TurnitinSettings | None
    grade_group_students_individually: bool
    external_tool_tag_attributes: None
    peer_reviews: bool
    automatic_peer_reviews: bool
    peer_review_count: int
    peer_reviews_assign_at: str
    intra_group_peer_reviews: bool
    group_category_id: int
    needs_grading_count: int
    needs_grading_count_by_section: list[NeedsGradingCountBySection]
    position: int
    post_to_sis: bool
    integration_id: str
    integration_data: IntegrationData
    points_possible: float
    submission_types: list[str]
    has_submitted_submissions: bool
    grading_type: str
    grading_standard_id: None
    published: bool
    unpublishable: bool
    only_visible_to_overrides: bool
    locked_for_user: bool
    lock_info: None
    lock_explanation: str
    quiz_id: int
    anonymous_submissions: bool
    discussion_topic: None
    freeze_on_copy: bool
    frozen: bool
    frozen_attributes: list[str]
    is_quiz_assignment: bool


class MediaComment(TypedDict):
    content_type: str
    display_name: str
    media_id: str
    media_type: str
    url: str


class SubmissionComment(TypedDict):
    id: int
    author_id: int
    author_name: str
    author: (
        str  # Assuming this would eventually contain a serialized UserDisplay object
    )
    comment: str
    created_at: str
    edited_at: str
    media_comment: str


class Submission(TypedDict):
    assignment_id: int
    assignment: None  # Replace with the appropriate type if you know it
    course: None  # Replace with the appropriate type if you know it
    attempt: int
    body: str
    grade: str
    grade_matches_current_submission: bool
    html_url: str
    preview_url: str
    score: float
    submission_comments: None  # Replace with the appropriate type if you know it
    submission_type: str
    submitted_at: str
    url: str | None
    user_id: int
    grader_id: int
    graded_at: str
    user: None  # Replace with the appropriate type if you know it
    late: bool
    assignment_visible: bool
    excused: bool
    missing: bool
    late_policy_status: str
    points_deducted: float
    seconds_late: int
    workflow_state: str
    extra_attempts: int
    anonymous_id: str
    posted_at: str
    read_status: str
    redo_request: bool


class EnrollmentUser(TypedDict):
    id: int
    name: str
    sortable_name: str
    short_name: str


class EnrollmentGrades(TypedDict):
    html_url: str
    current_score: float
    current_grade: str | None
    final_score: float
    final_grade: str | None


class Enrollment(TypedDict, total=False):
    id: int
    course_id: int
    sis_course_id: str | None
    course_integration_id: str | None
    course_section_id: int
    section_integration_id: str | None
    sis_account_id: str | None
    sis_section_id: str | None
    sis_user_id: str | None
    enrollment_state: str
    limit_privileges_to_course_section: bool
    sis_import_id: int | None
    root_account_id: int
    type: str
    user_id: int
    associated_user_id: int | None
    role: str
    role_id: int
    created_at: str
    updated_at: str
    start_at: str
    end_at: str
    last_activity_at: str
    last_attended_at: str
    total_activity_time: int
    html_url: str
    grades: EnrollmentGrades
    user: EnrollmentUser
    override_grade: str | None
    override_score: float | None
    unposted_current_grade: str | None
    unposted_final_grade: str | None
    unposted_current_score: str | None
    unposted_final_score: str | None
    has_grading_periods: bool | None
    totals_for_all_grading_periods_option: bool | None
    current_grading_period_title: str | None
    current_grading_period_id: int | None
    current_period_override_grade: str | None
    current_period_override_score: float | None
    current_period_unposted_current_score: float | None
    current_period_unposted_final_score: float | None
    current_period_unposted_current_grade: str | None
    current_period_unposted_final_grade: str | None


class User(TypedDict):
    id: int
    name: str
    sortable_name: str
    last_name: str
    first_name: str
    short_name: str
    sis_user_id: str
    sis_import_id: int
    integration_id: str
    login_id: str
    avatar_url: str
    avatar_state: str | None
    enrollments: list[Enrollment] | None  # Replace with the actual type if known
    email: str | None
    locale: str | None
    last_login: str | None
    time_zone: str | None
    bio: str | None


class AssignmentLite(TypedDict):
    name: str
    _id: str
    dueAt: str


class StudentLite(TypedDict):
    name: str
    _id: str


class AssignmentOverrideLite(TypedDict):
    dueAt: str | None
    _id: str
    students: list[StudentLite]


class AssignmentLiteOverrides(TypedDict):
    name: str
    _id: str
    dueAt: str | None
    published: bool
    lockAt: str | None
    pointsPossible: int
    assignmentOverrides: list[AssignmentOverrideLite]


class Folder(TypedDict):
    context_type: str
    context_id: int
    files_count: int
    position: int
    updated_at: str
    folders_url: str
    files_url: str
    full_name: str
    lock_at: str
    id: int
    folders_count: int
    name: str
    parent_folder_id: int
    created_at: str
    unlock_at: str | None  # Use Optional for values that can be None
    hidden: bool
    hidden_for_user: bool
    locked: bool
    locked_for_user: bool
    for_submissions: bool


class File(TypedDict):
    size: int
    content_type: str
    url: str
    id: int
    display_name: str
    created_at: str
    updated_at: str


class Canvas:
    """
    Asynchronous helper for the Canvas LMS API.
    """

    _course: Course | None

    def __init__(
        self,
        site_url: str,
        api_token: str,
        session: aiohttp.ClientSession,
        bot: CoordinateBot,
    ):
        self.site_url = site_url
        self.api_token = api_token
        self.session = session
        self.bot = bot
        self._course = None

    async def fetch(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        method: str = "get",
    ) -> Any:
        """
        Fetch a URL.
        """
        func = getattr(self.session, method)
        async with func(
            url,
            headers={"Authorization": f"Bearer {self.api_token}"},
            params=params,
        ) as resp:
            return await resp.json()

    async def get_course(self, course_id: int | None = None) -> Course:
        """
        Get a course by its ID.
        """
        if self._course:
            return self._course
        if not course_id:
            course = self.bot.get_course_info()
            if course:
                course_id = course.canvas_course_code
        if not course_id:
            raise ValueError("No course ID given and no current semester found.")
        url = f"{self.site_url}/api/v1/courses/{course_id}"
        course = await self.fetch(url)
        self._course = course
        return course

    async def get_sections(self, course_id: int) -> list:
        """
        Get all sections for a course.
        """
        sections = []
        page = 1
        has_more_data = True
        while has_more_data:
            url = f"{self.site_url}/api/v1/courses/{course_id}/sections"
            params = {"page": page, "per_page": 100}
            page_sections = await self.fetch(url, params=params, method="get")
            if page_sections:
                sections.extend(page_sections)
                page += 1
            else:
                has_more_data = False
        return sections

    async def get_thumbnail(self, name: str) -> str | None:
        """
        Return the thumbnail of a user with that name, or None if it no user can
        be found.
        """
        course = await self.get_course()
        canvas_users = await self.get_users(
            course,
            name,
            include=["avatar_url"],
        )
        if canvas_users:
            return canvas_users[0].get("avatar_url")

    async def get_syllabus(self, course_id: int) -> str:
        """
        Get the syllabus for a course and return the text content.
        """
        url = f"{self.site_url}/api/v1/courses/{course_id}?include[]=syllabus_body"
        syllabus_data = await self.fetch(url)
        if "syllabus_body" in syllabus_data:
            html_content = syllabus_data["syllabus_body"]
            soup = BeautifulSoup(html_content, "html.parser")
            text = soup.get_text(separator=" ", strip=True)
            return text
        else:
            return "Syllabus content not found."

    async def get_course_schedule(self, course_id: int) -> str:
        """
        Get the course schedule for a course and return the text content.
        """
        url = f"{self.site_url}/api/v1/courses/{course_id}/pages/course-schedule"
        schedule_data = await self.fetch(url)
        if "body" in schedule_data:
            html_content = schedule_data["body"]
            soup = BeautifulSoup(html_content, "html.parser")
            text = soup.get_text(separator=" ", strip=True)
            return text
        else:
            return "Course schedule content not found."

    async def get_assignments(
        self,
        course: Course,
        *,
        order_by: str = "due_at",
    ) -> list[Assignment]:
        """
        Get all assignments for a course.
        """
        url = f"{self.site_url}/api/v1/courses/{course['id']}/assignments?order_by={order_by}"
        return await self.fetch(url)

    async def get_files_in_folder(self, folder_id: int) -> list[File]:
        """
        Get all files in a folder.
        """
        url = f"{self.site_url}/api/v1/folders/{folder_id}/files"
        return await self.fetch(url)

    async def resolve_path(self, folder_name: str) -> list[Folder]:
        """
        Resolve a folder path to its ID.
        """
        course = await self.get_course()
        url = f"{self.site_url}/api/v1/courses/{course['id']}/folders/by_path/{folder_name}"
        return await self.fetch(url)

    async def get_file_content(self, url: str) -> str:
        """
        Get the content of a file.
        """
        async with self.session.get(url) as resp:
            return await resp.text()

    async def get_assignments_lite(
        self,
        course: Course,
    ) -> list[AssignmentLite]:
        """
        Gets very basic info about all assignments for a course.
        """
        url = f"{self.site_url}/api/graphql"
        query = """
            query MyQuery($courseId: ID!) {
              course(id: $courseId) {
                assignmentsConnection {
                  nodes {
                    name
                    _id
                    dueAt(applyOverrides: false)
                  }
                }
              }
            }
        """
        resp = await self.fetch(
            url,
            method="post",
            params={"query": query, "variables[courseId]": course["id"]},
        )
        return list(resp["data"]["course"]["assignmentsConnection"]["nodes"])

    async def get_assignments_with_overrides(
        self,
        course_id: int,
    ) -> list[AssignmentLiteOverrides]:
        url = f"{self.site_url}/api/graphql"
        query = """
        query MyQuery($courseId: ID!) {
          course(id: $courseId) {
            assignmentsConnection {
              nodes {
                name
                _id
                dueAt(applyOverrides: false)
                published
                assignmentOverrides {
                  edges {
                    node {
                      dueAt
                      _id
                      set {
                        ... on Group {
                          id
                          name
                        }
                        ... on AdhocStudents {
                          __typename
                          students {
                            name
                            _id
                          }
                        }
                      }
                      lockAt
                    }
                  }
                }
                lockAt
                pointsPossible
              }
            }
          }
        }
        """
        resp = await self.fetch(
            url,
            method="post",
            params={"query": query, "variables[courseId]": course_id},
        )
        nodes = list(resp["data"]["course"]["assignmentsConnection"]["nodes"])
        for node in nodes:
            overrides = []
            for edge in node["assignmentOverrides"]["edges"]:
                override = {}
                override["dueAt"] = edge["node"]["dueAt"]
                override["_id"] = edge["node"]["_id"]
                if edge["node"]["set"]["__typename"] == "AdhocStudents":
                    override["students"] = list(edge["node"]["set"]["students"])
                overrides.append(override)
            node["assignmentOverrides"] = overrides
        return nodes

    async def get_users(
        self,
        course: Course,
        search_term: str,
        *,
        enrollment_type: list[str] = [],
        include: list[str] = [],
    ) -> list[User]:
        """
        Get all users for a course
        """
        users = []
        page = 1
        per_page = 100

        while True:
            url = f"{self.site_url}/api/v1/courses/{course['id']}/search_users"
            response = await self.fetch(
                url,
                params={
                    "search_term": search_term,
                    "include[]": include,
                    "enrollment_type[]": enrollment_type,
                    "page": page,
                    "per_page": per_page,
                },
            )
            users.extend(response)

            if len(response) < per_page:
                break
            page += 1
        return users

    async def get_submission(
        self,
        course_id: int,
        assignment: Assignment,
        user_id: int,
    ) -> Submission:
        """
        Get a submission for a user.
        """
        url = f"{self.site_url}/api/v1/courses/{course_id}/assignments/{assignment['id']}/submissions/{user_id}"
        return await self.fetch(url)

    async def update_time_allowed(
        self,
        course_id: int,
        user_id: int,
        quiz_id: int,
        extra_time: float,
    ):
        """
        Grants a student extra time on a quiz. The amount of extra time is in minutes.
        """
        data = {
            "quiz_extensions[][user_id]": user_id,
            "quiz_extensions[][extra_time]": extra_time,
        }
        url = f"{self.site_url}/api/v1/courses/{course_id}/quizzes/{quiz_id}/extensions"
        return await self.fetch(url, method="post", params=data)

    async def extend_due_date(
        self,
        course_id: int,
        assignment_id: str,
        student_id: int,
        new_date: datetime.datetime,
        existing_override_id: str | None = None,
    ):
        """
        Creates a new Canvas assignment override granting a student an extended due
        date.
        """
        # Create assignment override for that student
        data = {
            "assignment_override[student_ids][]": student_id,
            "assignment_override[title]": "Extended due date",
            "assignment_override[due_at]": new_date.isoformat(),
            "assignment_override[unlock_at]": datetime.datetime.now().isoformat(),
            "assignment_override[lock_at]": new_date.isoformat(),
        }
        url = f"{self.site_url}/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides"
        if existing_override_id:
            url += f"/{existing_override_id}"
            return await self.fetch(url, method="put", params=data)
        return await self.fetch(url, method="post", params=data)

    async def send_message(self, user_id: int, subject: str, body: str):
        data = {
            "recipients[]": user_id,
            "subject": subject,
            "body": body,
        }
        url = f"{self.site_url}/api/v1/conversations"
        return await self.fetch(url, method="post", params=data)

    async def find_canvas_users(self, student_sys_id: str) -> list[User]:
        course = await self.get_course()
        users = await self.get_users(
            course,
            search_term=student_sys_id,
            include=["enrollments"],
        )
        user_list = list(users)
        logger.info(
            f"List of users found with student ID {student_sys_id[:3]}***** in course ID {course['id']}: {[str(u) for u in user_list]}",
        )
        return user_list

    def permutations_given_name(self, name: str) -> list[str]:
        names = name.split(" ")
        variations = []
        if len(names) > 1:
            variations.append(f"{names[1]} {names[0]}".lower())
        if len(names) > 2:
            variations.append(f"{names[1]} {names[2]}".lower())
            variations.append(f"{names[2]} {names[0]}".lower())
            variations.append(f"{names[1]} {names[0]}".lower())
            variations.append(f"{names[2]} {names[1]}".lower())
            variations.append(f"{names[0]} {names[1]}".lower())
            variations.append(f"{names[0]} {names[2]}".lower())
        if len(names) > 3:
            variations.extend(
                [
                    f"{name[0]} {name[1]}".lower()
                    for name in itertools.permutations(names, 2)
                ],
            )
            variations.extend(
                [
                    f"{name[0]} {name[1]} {name[2]}".lower()
                    for name in itertools.permutations(names, 3)
                ],
            )
            variations.extend(
                [
                    f"{name[0]} {name[1]} {name[2]} {name[3]}".lower()
                    for name in itertools.permutations(names, 4)
                ],
            )
        return variations

    def verify_enrollment(self, users: list[User], ufid: str, name: str) -> bool:
        ufid = ufid.lower().strip()
        name = name.strip()
        variations = self.permutations_given_name(name)
        for user in users:
            try:
                if user["sis_user_id"] == ufid and (
                    user["name"].lower() == name.lower() or name.lower() in variations
                ):
                    return True
            except Exception:
                import traceback

                traceback.print_exc()
        return False
