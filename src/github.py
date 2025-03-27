from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    from aiohttp import ClientSession


logger = logging.getLogger(__name__)


class User(TypedDict):
    login: str
    id: int
    node_id: str
    avatar_url: str
    gravatar_id: str
    url: str
    html_url: str
    followers_url: str
    following_url: str
    gists_url: str
    starred_url: str
    subscriptions_url: str
    organizations_url: str
    repos_url: str
    events_url: str
    received_events_url: str
    type: str
    site_admin: bool
    name: str | None
    company: str | None
    blog: str | None
    location: str | None
    email: str | None
    hireable: bool | None
    bio: str | None
    twitter_username: str | None
    public_repos: int
    public_gists: int
    followers: int
    following: int
    created_at: str
    updated_at: str


class Label(TypedDict):
    id: int
    node_id: str
    url: str
    name: str
    description: str
    color: str
    default: bool


class Milestone(TypedDict):
    url: str
    html_url: str
    labels_url: str
    id: int
    node_id: str
    number: int
    state: str
    title: str
    description: str
    creator: User
    open_issues: int
    closed_issues: int
    created_at: str
    updated_at: str
    closed_at: str | None
    due_on: str


class PullRequest(TypedDict):
    url: str
    html_url: str
    diff_url: str
    patch_url: str


class RepositoryPermissions(TypedDict):
    admin: bool
    push: bool
    pull: bool


class RepositorySecurityAndAnalysisItem(TypedDict):
    status: str


class RepositorySecurityAndAnalysis(TypedDict):
    advanced_security: RepositorySecurityAndAnalysisItem
    secret_scanning: RepositorySecurityAndAnalysisItem
    secret_scanning_push_protection: RepositorySecurityAndAnalysisItem


class Repository(TypedDict):
    id: int
    node_id: str
    name: str
    full_name: str
    owner: User
    private: bool
    html_url: str
    description: str | None
    fork: bool
    url: str
    archive_url: str
    assignees_url: str
    blobs_url: str
    branches_url: str
    collaborators_url: str
    comments_url: str
    commits_url: str
    compare_url: str
    contents_url: str
    contributors_url: str
    deployments_url: str
    downloads_url: str
    events_url: str
    forks_url: str
    git_commits_url: str
    git_refs_url: str
    git_tags_url: str
    git_url: str
    issue_comment_url: str
    issue_events_url: str
    issues_url: str
    keys_url: str
    labels_url: str
    languages_url: str
    merges_url: str
    milestones_url: str
    notifications_url: str
    pulls_url: str
    releases_url: str
    ssh_url: str
    stargazers_url: str
    statuses_url: str
    subscribers_url: str
    subscription_url: str
    tags_url: str
    teams_url: str
    trees_url: str
    clone_url: str
    mirror_url: str
    hooks_url: str
    svn_url: str
    homepage: str | None
    language: str | None
    forks_count: int
    stargazers_count: int
    watchers_count: int
    size: int
    default_branch: str
    open_issues_count: int
    is_template: bool
    topics: list[str]
    has_issues: bool
    has_projects: bool
    has_wiki: bool
    has_pages: bool
    has_downloads: bool
    has_discussions: bool
    archived: bool
    disabled: bool
    visibility: str
    pushed_at: str
    created_at: str
    updated_at: str
    permissions: RepositoryPermissions
    security_and_analysis: RepositorySecurityAndAnalysis


class Issue(TypedDict):
    id: int
    node_id: str
    url: str
    repository_url: str
    labels_url: str
    comments_url: str
    events_url: str
    html_url: str
    number: int
    state: str
    title: str
    body: str
    user: User
    labels: list[Label]
    assignee: User
    assignees: list[User]
    milestone: Milestone
    locked: bool
    active_lock_reason: str
    comments: int
    pull_request: PullRequest
    closed_at: str | None
    created_at: str
    updated_at: str
    closed_by: User | None
    author_association: str
    state_reason: str | None


class GitHub:
    def __init__(self, *, auth_token: str | None, session: ClientSession):
        self.auth_token = auth_token
        self.session = session

    async def fetch(
        self,
        url: str,
        *,
        method: Literal["GET", "POST"] = "GET",
        extra_headers: dict[str, str] | None = None,
        data: dict[str, Any] | str | None = None,
        json_data: dict[str, Any] | None = None,
    ):
        """
        Fetches a URL with the given method and headers.

        Raises ClientResponseError if the response status is not 2xx.
        """
        if not self.auth_token:
            raise RuntimeError("No GitHub auth_token provided")

        headers = {
            "Authorization": f"Bearer {self.auth_token}",
        }
        if json_data:
            data = json.dumps(json_data)
        if extra_headers:
            headers.update(extra_headers)
        async with self.session.request(
            method,
            url,
            headers=headers,
            data=data,
        ) as response:
            if not response.ok:
                logger.error(
                    f"Error fetching GitHub url {url}: {await response.json()}",
                )
            return await response.json()

    async def create_issue(self, issue_title: str, issue_body: str) -> Issue:
        """
        Creates an issue on the repo with the given title and body.
        """
        return await self.fetch(
            "https://api.github.com/repos/cop3503-ufl/coordinate/issues",
            method="POST",
            json_data={
                "title": issue_title,
                "body": issue_body,
            },
        )
