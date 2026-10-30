"""The collab calls of `FakeGateway`, in memory."""
from __future__ import annotations

import re

from jira_dc_cli.domain.collab import RemoteLink, RemoteLinkRef, Votes, Watchers, Worklog, WorklogPage
from jira_dc_cli.domain.errors import NotFoundError
from jira_dc_cli.domain.model import User
from jira_dc_cli.domain.worktime import WorklogRequest

JANE = User("jdoe", "Jane Doe", "jane.doe@example.com")
ALEX = User("asmith", "Alex Smith", "alex.smith@example.com")
BEN = User("bwong", "Ben Wong", "ben.wong@example.com")
UNITS = {"w": 5 * 8 * 3600, "d": 8 * 3600, "h": 3600, "m": 60}  # the working time that Jira has unless an administrator changed it


def seconds_of(text: str) -> int:
    return sum(int(amount) * UNITS[unit] for amount, unit in re.findall(r"(\d+)([wdhm])", text))


class FakeCollab:
    """Implements `CollabPort` for the tests; its state lives on the gateway (every name starts with `collab_`, so that no area of
    the gateway meets another's) and is set up in `_init_collab`."""

    def _init_collab(self) -> None:
        """Called at the end of `FakeGateway.__init__`."""
        self.collab_users = {user.name: user for user in (JANE, ALEX, BEN)}  # the users that exist
        self.collab_watchers: dict[str, list[User]] = {"PROJ-1": [ALEX, BEN]}
        self.collab_watchers_listed = True  # False: Jira lists nobody, as for a user who may not see the watchers
        self.collab_voters: dict[str, set[str]] = {"PROJ-1": {"asmith"}}
        self.collab_vote_refusal = ""  # a reason why Jira refuses to take a vote (the issue is yours, voting is off)
        self.collab_worklogs: dict[str, list[Worklog]] = {
            "PROJ-1": [
                Worklog(
                    "10100", "2026-10-14T09:00:00.000+0000", "1h 30m", 5400, "Reviewed with [~asmith] (alex.smith@example.com)", ALEX,
                    "2026-10-14T10:40:00.000+0000",
                ),
                Worklog("10101", "2026-10-18T13:00:00.000+0000", "2h", 7200, "", JANE, "2026-10-18T15:05:00.000+0000"),
            ]
        }
        self.collab_worklog_total_extra = 0  # added to the total that Jira states, to play a server that returns less than it holds
        self.collab_remote_links: dict[str, list[RemoteLink]] = {
            "PROJ-1": [RemoteLink("10000", "Runbook", "https://wiki.example.com/runbook", "documentation", "", "wiki:runbook")]
        }
        self.collab_next_id = 10200

    def get_watchers(self, key: str) -> Watchers:
        self._enter("get_watchers", key)
        self._issue(key)
        users = list(self.collab_watchers.get(key, []))
        watching = any(user.name == self.user.name for user in users)
        return Watchers(len(users), watching, users if self.collab_watchers_listed else None)

    def add_watcher(self, key: str, payload: str) -> None:
        self._enter("add_watcher", key, payload)
        self._issue(key)
        if payload not in self.collab_users:
            raise NotFoundError(f"Not found: POST /issue/{key}/watchers - The user named '{payload}' does not exist.")
        users = self.collab_watchers.setdefault(key, [])
        if all(user.name != payload for user in users):
            users.append(self.collab_users[payload])

    def remove_watcher(self, key: str, query: dict[str, str]) -> None:
        self._enter("remove_watcher", key, query)
        self._issue(key)
        name = query["username"]
        self.collab_watchers[key] = [user for user in self.collab_watchers.get(key, []) if user.name != name]

    def get_votes(self, key: str) -> Votes:
        self._enter("get_votes", key)
        self._issue(key)
        voters = self.collab_voters.get(key, set())
        return Votes(len(voters), self.user.name in voters)

    def add_vote(self, key: str) -> None:
        self._enter("add_vote", key)
        self._issue(key)
        if self.collab_vote_refusal:
            raise NotFoundError(f"Not found: POST /issue/{key}/votes - {self.collab_vote_refusal}")
        self.collab_voters.setdefault(key, set()).add(self.user.name)

    def remove_vote(self, key: str) -> None:
        self._enter("remove_vote", key)
        self._issue(key)
        if self.user.name not in self.collab_voters.get(key, set()):
            raise NotFoundError(f"Not found: DELETE /issue/{key}/votes - You have not voted for this issue.")
        self.collab_voters[key].discard(self.user.name)

    def list_worklogs(self, key: str) -> WorklogPage:
        self._enter("list_worklogs", key)
        self._issue(key)
        entries = list(self.collab_worklogs.get(key, []))
        return WorklogPage(len(entries) + self.collab_worklog_total_extra, entries)

    def add_worklog(self, key: str, request: WorklogRequest) -> Worklog:
        self._enter("add_worklog", key, request)
        self._issue(key)
        self.collab_next_id += 1
        body = request.body
        entry = Worklog(
            str(self.collab_next_id),
            body.get("started", "2026-10-20T12:00:00.000+0000"),
            body["timeSpent"],
            seconds_of(body["timeSpent"]),
            body.get("comment", ""),
            self.user,
            "2026-10-20T12:00:00.000+0000",
        )
        self.collab_worklogs.setdefault(key, []).append(entry)
        return entry

    def list_remote_links(self, key: str) -> list[RemoteLink]:
        self._enter("list_remote_links", key)
        self._issue(key)
        return list(self.collab_remote_links.get(key, []))

    def save_remote_link(self, key: str, payload: dict) -> RemoteLinkRef:
        self._enter("save_remote_link", key, payload)
        self._issue(key)
        links = self.collab_remote_links.setdefault(key, [])
        target = payload["object"]
        global_id = payload.get("globalId", "")
        for index, link in enumerate(links):
            if global_id and link.global_id == global_id:
                links[index] = RemoteLink(link.id, target["title"], target["url"], payload.get("relationship", ""), link.application, global_id)
                return RemoteLinkRef(link.id)
        self.collab_next_id += 1
        links.append(RemoteLink(str(self.collab_next_id), target["title"], target["url"], payload.get("relationship", ""), "", global_id))
        return RemoteLinkRef(str(self.collab_next_id))
