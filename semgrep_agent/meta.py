import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional
from typing import Type
from typing import Union

import click
import git
import sh
from boltons.cacheutils import cachedproperty
from glom import glom
from glom import T
from glom.core import TType

from .utils import debug_echo


@dataclass
class GitMeta:
    """Gather metadata only from local filesystem."""

    ctx: click.Context
    environment = "git"

    @cachedproperty
    def event_name(self) -> str:
        return "unknown"

    @cachedproperty
    def repo(self) -> git.Repo:  # type: ignore
        repo = git.Repo()
        debug_echo(f"found repo: {repo!r}")
        return repo

    @cachedproperty
    def commit_sha(self) -> Optional[str]:
        return self.repo.head.commit.hexsha  # type: ignore

    @cachedproperty
    def commit(self) -> git.Commit:  # type: ignore
        commit = self.repo.commit(self.commit_sha)
        debug_echo(f"found commit: {commit!r}")
        return commit

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repository": None,
            "ci_job_url": None,
            "environment": self.environment,
            "commit": self.commit_sha,
            "commit_committer_email": self.repo.head.commit.committer.email,
            "commit_timestamp": self.commit.committed_datetime.isoformat(),
            "commit_author_email": self.repo.head.commit.author.email,
            "commit_author_name": self.repo.head.commit.author.name,
            "commit_author_username": None,
            "commit_author_image_url": None,
            "commit_authored_timestamp": self.commit.authored_datetime.isoformat(),
            "commit_title": self.commit.summary,
            "config": self.ctx.obj.config,
            "on": self.event_name,
            "branch": None,
            "pull_request_timestamp": None,
            "pull_request_author_username": None,
            "pull_request_author_image_url": None,
            "pull_request_id": None,
            "pull_request_title": None,
            "semgrep_version": sh.semgrep(version=True).strip(),
            "bento_version": sh.bento(version=True).strip(),
            "python_version": sh.python(version=True).strip(),
        }


@dataclass
class GithubMeta(GitMeta):
    """Gather metadata from GitHub Actions."""

    environment = "github-actions"

    def glom_event(self, spec: TType) -> Any:
        return glom(self.event, spec, default=None)

    @cachedproperty
    def event(self) -> Dict[str, Any]:
        if value := os.getenv("GITHUB_EVENT_PATH"):
            debug_echo(f"found github event data at {value}")
            return json.loads(Path(value).read_text())  # type: ignore
        return {}

    @cachedproperty
    def repo_name(self) -> Optional[str]:
        return os.getenv("GITHUB_REPOSITORY")

    @cachedproperty
    def repo_url(self) -> Optional[str]:
        if self.repo_name:
            return f"https://github.com/{self.repo_name}"
        return None

    @cachedproperty
    def commit_sha(self) -> Optional[str]:
        if self.event_name == "pull_request":
            # https://github.community/t/github-sha-not-the-same-as-the-triggering-commit/18286/2
            return self.glom_event(T["pull_request"]["head"]["sha"])  # type: ignore
        if self.event_name == "push":
            return os.getenv("GITHUB_SHA")
        return super().commit_sha  # type: ignore

    @cachedproperty
    def commit_ref(self) -> Optional[str]:
        return os.getenv("GITHUB_REF")

    @cachedproperty
    def ci_actor(self) -> Optional[str]:
        return os.getenv("GITHUB_ACTOR")

    @cachedproperty
    def ci_job_url(self) -> Optional[str]:
        if self.repo_url and (value := os.getenv("GITHUB_RUN_ID")):
            return f"{self.repo_url}/actions/runs/{value}"
        return None

    @cachedproperty
    def event_name(self) -> str:
        return os.getenv("GITHUB_EVENT_NAME", "unknown")

    def to_dict(self) -> Dict[str, Any]:
        return {
            **super().to_dict(),
            "repository": self.repo_name,
            "branch": self.commit_ref,
            "ci_job_url": self.ci_job_url,
            "commit_author_username": self.glom_event(T["sender"]["login"]),
            "commit_author_image_url": self.glom_event(T["sender"]["avatar_url"]),
            "pull_request_timestamp": self.glom_event(T["pull_request"]["created_at"]),
            "pull_request_author_username": self.glom_event(
                T["pull_request"]["user"]["login"]
            ),
            "pull_request_author_image_url": self.glom_event(
                T["pull_request"]["user"]["avatar_url"]
            ),
            "pull_request_id": self.glom_event(T["pull_request"]["number"]),
            "pull_request_title": self.glom_event(T["pull_request"]["title"]),
        }


@dataclass
class GitlabMeta(GitMeta):
    """Gather metadata from GitLab 10.0+"""

    environment = "gitlab-ci"

    @cachedproperty
    def repo_name(self) -> Optional[str]:
        return os.getenv("CI_PROJECT_PATH")

    @cachedproperty
    def repo_url(self) -> Optional[str]:
        return os.getenv("CI_PROJECT_URL")

    @cachedproperty
    def commit_sha(self) -> Optional[str]:
        return os.getenv("CI_COMMIT_SHA")

    @cachedproperty
    def commit_ref(self) -> Optional[str]:
        return os.getenv("CI_COMMIT_REF_NAME")

    @cachedproperty
    def ci_actor(self) -> Optional[str]:
        return os.getenv("GITLAB_USER_LOGIN")

    @cachedproperty
    def ci_job_url(self) -> Optional[str]:
        return os.getenv("CI_JOB_URL")

    @cachedproperty
    def event_name(self) -> str:
        return os.getenv("CI_PIPELINE_SOURCE", "unknown")

    @cachedproperty
    def mr_id(self) -> Optional[str]:
        return os.getenv("CI_MERGE_REQUEST_IID")

    @cachedproperty
    def mr_title(self) -> Optional[str]:
        return os.getenv("CI_MERGE_REQUEST_TITLE")

    def to_dict(self) -> Dict[str, Any]:
        return {
            **super().to_dict(),
            "repository": self.repo_name,
            "ci_job_url": self.ci_job_url,
            "on": self.event_name,
            "branch": self.commit_ref,
            "pull_request_id": self.mr_id,
            "pull_request_title": self.mr_title,
        }


def detect_meta_environment() -> Type[GitMeta]:
    # https://help.github.com/en/actions/configuring-and-managing-workflows/using-environment-variables
    if os.getenv("GITHUB_ACTIONS") == "true":
        return GithubMeta

    # https://circleci.com/docs/2.0/env-vars/#built-in-environment-variables
    elif os.getenv("CIRCLECI") == "true":  # nosem
        return GitMeta

    # https://docs.travis-ci.com/user/environment-variables/#default-environment-variables
    elif os.getenv("TRAVIS") == "true":  # nosem
        return GitMeta

    # https://docs.gitlab.com/ee/ci/variables/predefined_variables.html
    elif os.getenv("GITLAB_CI") == "true":
        return GitlabMeta

    elif os.getenv("CI"):  # nosem
        return GitMeta

    else:  # nosem
        return GitMeta