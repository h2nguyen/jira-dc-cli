"""Commands of the agile area: boards, sprints, the review of a sprint, velocity, the backlog, epics, ranking, and the writes that move
issues and create or change sprints. Everything goes through the Agile API of Jira Software and the platform search; no `api` call."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from .....domain.agile import BOARD_TYPES, day
from .....domain.errors import UsageError
from .....domain.sprints import DERIVATION_NOTES
from ..listing import FORMAT, SELECT, print_list
from ..render import key_values, number, table, to_jsonable
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import pretty

if TYPE_CHECKING:
    from .....application.agile import (
        BacklogView,
        BoardList,
        EpicList,
        MoveResult,
        RankResult,
        SprintList,
        SprintMoveResult,
        SprintView,
        SprintWrite,
        VelocityResult,
    )
    from .....domain.agile import Board, Epic, SprintInfo
    from .....domain.model import Issue
    from .....domain.sprints import SprintReview

GROUP = "Agile"

BOARD = Arg(("--board",), "Board id (`jira boards` lists them). Default: the scrum board of the project.", kind="int", metavar="ID")
PROJECT = Arg(("--project",), "Project key; its scrum board is used. Default: the current project (`jira use`).", metavar="KEY")
DRY_RUN = Arg(("--dry-run",), "Read the targets, show the payload that would be sent, and send nothing.", kind="flag")
IF_STATE = Arg(
    ("--if-state",), "Refuse (exit code 3) unless the sprint is in this state now: future, active or closed; several with commas.", metavar="STATE"
)
SINCE = Arg(("--if-unchanged-since",), "Refuse (exit code 3) when one of the issues changed in Jira after this time (ISO 8601).", metavar="ISO")


# -- the rows of the lists, in the shape of their JSON ---------------------------------------------


def sprint_json(sprint: SprintInfo) -> dict[str, Any]:
    return {
        "id": sprint.id,
        "name": sprint.name,
        "state": sprint.state,
        "startDate": sprint.start_date,
        "endDate": sprint.end_date,
        "completeDate": sprint.complete_date,
        "goal": sprint.goal,
        "boardId": sprint.board_id,
    }


def issue_json(issue: Issue) -> dict[str, Any]:
    status = {"name": issue.status.name, "category": issue.status.category} if issue.status else None
    return {
        "key": issue.key,
        "issueType": issue.issue_type,
        "status": status,
        "storyPoints": issue.story_points,
        "epicKey": issue.epic_key,
        "summary": issue.summary,
    }


def _points(value: float | None) -> str:
    return "" if value is None else number(value)


def _issues_table(issues: list[Issue]) -> str:
    rows = [(i.key, i.issue_type, i.status.name if i.status else "", _points(i.story_points), i.epic_key, i.summary) for i in issues]
    return table(["KEY", "TYPE", "STATUS", "POINTS", "EPIC", "SUMMARY"], rows)


def _board_header(board: Board) -> dict[str, Any]:
    return {"boardId": board.id, "boardName": board.name}


# -- boards --------------------------------------------------------------------------------------

BOARD_COLUMNS = [("ID", "id"), ("NAME", "name"), ("TYPE", "type"), ("PROJECTS", "projects")]


def boards_text(result: BoardList) -> str:
    if not result.boards:
        return "No boards found."
    return table(["ID", "NAME", "TYPE", "PROJECTS"], [(b.id, b.name, b.type, ", ".join(b.projects)) for b in result.boards])


def run_boards(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.agile.list_boards.execute(project=args.project or "", board_type=args.type or "", name=args.name or "", limit=args.limit)
    rows = [{"id": b.id, "name": b.name, "type": b.type, "projects": list(b.projects)} for b in result.boards]
    document = {"total": result.total, "count": len(rows), "boards": rows}
    print_list(ctx, args, payload=document, rows=rows, list_key="boards", columns=BOARD_COLUMNS, plain=lambda: boards_text(result))
    if len(rows) < result.total:
        ctx.diagnostics.info(f"Showing {len(rows)} of {result.total} boards. Narrow it with --project, --type or --name, or raise --limit.")
    return 0


# -- sprints -------------------------------------------------------------------------------------

SPRINT_COLUMNS = [
    ("ID", "id"),
    ("NAME", "name"),
    ("STATE", "state"),
    ("START", "startDate"),
    ("END", "endDate"),
    ("COMPLETED", "completeDate"),
    ("GOAL", "goal"),
]


def sprints_text(result: SprintList) -> str:
    if not result.sprints:
        return "No sprints found."
    rows = [(s.id, s.name, s.state, day(s.start_date), day(s.end_date), day(s.complete_date), s.goal) for s in result.sprints]
    return table([header for header, _ in SPRINT_COLUMNS], rows)


def run_sprints(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.agile.list_sprints.execute(board=args.board, project=args.project or "", states=args.state or "", limit=args.limit)
    rows = [sprint_json(s) for s in result.sprints]
    document = {**_board_header(result.board), "total": result.total, "count": len(rows), "sprints": rows}
    print_list(ctx, args, payload=document, rows=rows, list_key="sprints", columns=SPRINT_COLUMNS, plain=lambda: sprints_text(result))
    if len(rows) < result.total:
        shown = f"Showing {len(rows)} of {result.total} sprints (active and future first, then the newest closed ones)."
        ctx.diagnostics.info(f"{shown} Use --state or --limit.")
    return 0


def sprint_text(sprint: SprintInfo) -> str:
    return key_values(
        [
            ("Sprint", f"{sprint.name} (id {sprint.id})"),
            ("State", sprint.state),
            ("Board", sprint.board_id),
            ("Start", sprint.start_date),
            ("End", sprint.end_date),
            ("Completed", sprint.complete_date),
            ("Goal", sprint.goal),
        ]
    )


REVIEW_FOOTNOTE = "* The history of this sprint is incomplete (see the notes): the marked numbers may be too low or wrong."
VELOCITY_FOOTNOTE = "* The history of the marked sprints is incomplete (see the warnings): their committed numbers may be too low or wrong."


def review_text(review: SprintReview) -> str:
    # committed, added and removed are the groups that rest on the history of the field Sprint; an incomplete history marks them
    groups = (
        ("Committed", review.committed, True),
        ("Completed", review.completed, False),
        ("Added", review.added, True),
        ("Removed", review.removed, True),
        ("Carried over", review.carried_over, False),
    )
    mark = "" if review.history_complete else "*"
    # a zero must be text: `render.table` drops a cell that is falsy
    rows = [
        (name + (mark if from_history else ""), str(g.issues), number(g.points), str(g.without_points), ", ".join(g.keys))
        for name, g, from_history in groups
    ]
    scope = f"{review.start} to {review.end}" + ("" if review.ended else " (now: the sprint is still running)")
    lines = [f"Review of the sprint, {scope}", table(["GROUP", "ISSUES", "POINTS", "NO POINTS", "KEYS"], rows)]
    if mark:
        lines.append(REVIEW_FOOTNOTE)
    return "\n".join([*lines, "", "Notes:", *(f"- {note}" for note in review.notes)])


def sprint_view_text(view: SprintView) -> str:
    parts = [sprint_text(view.sprint)]
    if view.issues is not None:
        parts.append(f"Issues in the sprint: {len(view.issues)}\n" + (_issues_table(view.issues) if view.issues else ""))
    if view.review is not None:
        parts.append(review_text(view.review))
    return "\n\n".join(part.rstrip() for part in parts)


def run_sprint(ctx: Context, args: argparse.Namespace) -> int:
    view = ctx.app.agile.show_sprint.execute(
        args.sprint, board=args.board, project=args.project or "", issues=args.issues, review=args.review, with_subtasks=args.with_subtasks
    )
    if ctx.json_mode:
        issues = None if view.issues is None else [issue_json(issue) for issue in view.issues]
        ctx.print_json({"sprint": sprint_json(view.sprint), "issues": issues, "review": view.review})
    else:
        ctx.print(sprint_view_text(view))
    return 0


# -- velocity, backlog, epics ----------------------------------------------------------------------

VELOCITY_COLUMNS = [
    ("ID", "id"),
    ("NAME", "name"),
    ("COMPLETED ON", "completeDate"),
    ("COMMITTED POINTS", "committedPoints"),
    ("COMPLETED POINTS", "completedPoints"),
    ("COMMITTED ISSUES", "committedIssues"),
    ("COMPLETED ISSUES", "completedIssues"),
]
# the table marks an incomplete history in its cells; TSV and Markdown get a column for it
VELOCITY_LIST_COLUMNS = [*VELOCITY_COLUMNS, ("HISTORY COMPLETE", "historyComplete")]


def _left_out_text(count: int) -> str:
    return f"{count} closed sprint{'' if count == 1 else 's'} could not be reviewed (see the warnings)."


def run_velocity(ctx: Context, args: argparse.Namespace) -> int:
    result: VelocityResult = ctx.app.agile.velocity.execute(board=args.board, project=args.project or "", last=args.last)
    rows = [to_jsonable(row) for row in result.rows]
    document = {
        **_board_header(result.board),
        "count": len(rows),
        "averageCompleted": result.average_completed,
        "sprints": rows,
        "leftOut": result.left_out,
        "notes": result.notes,
    }

    def plain() -> str:
        if not rows:
            return _left_out_text(len(result.left_out)) if result.left_out else "No closed sprints."
        # the committed numbers of a sprint with an incomplete history are marked, so that nobody takes them for exact
        body = [
            (
                str(r["id"]),
                r["name"],
                day(r["completeDate"]),
                number(r["committedPoints"]) + ("" if r["historyComplete"] else "*"),
                number(r["completedPoints"]),
                str(r["committedIssues"]) + ("" if r["historyComplete"] else "*"),
                str(r["completedIssues"]),
            )
            for r in rows
        ]
        lines = [
            table([header for header, _ in VELOCITY_COLUMNS], body),
            "",
            f"Average completed: {number(result.average_completed or 0)} points over {len(rows)} sprint(s), the newest first.",
        ]
        if not all(r["historyComplete"] for r in rows):
            lines.append(VELOCITY_FOOTNOTE)
        if result.left_out:
            lines.append(_left_out_text(len(result.left_out)))
        return "\n".join(lines)

    print_list(ctx, args, payload=document, rows=rows, list_key="sprints", columns=VELOCITY_LIST_COLUMNS, plain=plain)
    for note in result.notes:
        if note not in DERIVATION_NOTES:  # how the numbers are derived is in the help and the guide; what is wrong with them is said here
            ctx.diagnostics.warn(note)
    return 0


BACKLOG_COLUMNS = [
    ("KEY", "key"),
    ("TYPE", "issueType"),
    ("STATUS", "status.name"),
    ("POINTS", "storyPoints"),
    ("EPIC", "epicKey"),
    ("SUMMARY", "summary"),
]


def run_backlog(ctx: Context, args: argparse.Namespace) -> int:
    result: BacklogView = ctx.app.agile.show_backlog.execute(board=args.board, project=args.project or "", limit=args.limit)
    rows = [issue_json(issue) for issue in result.issues]
    document = {**_board_header(result.board), "total": result.total, "count": len(rows), "issues": rows}

    def plain() -> str:
        return _issues_table(result.issues) if result.issues else "The backlog is empty."

    print_list(ctx, args, payload=document, rows=rows, list_key="issues", columns=BACKLOG_COLUMNS, plain=plain)
    if len(rows) < result.total:
        ctx.diagnostics.info(f"Showing the first {len(rows)} of {result.total} issues, in the order of the rank. Use --limit.")
    return 0


EPIC_COLUMNS = [("KEY", "key"), ("NAME", "name"), ("DONE", "done"), ("SUMMARY", "summary")]


def epics_text(result: EpicList) -> str:
    if not result.epics:
        return "No epics found."
    return table(["KEY", "NAME", "DONE", "SUMMARY"], [(e.key, e.name, "yes" if e.done else "no", e.summary) for e in result.epics])


def epic_json(epic: Epic) -> dict[str, Any]:
    return {"key": epic.key, "name": epic.name, "summary": epic.summary, "done": epic.done}


def run_epics(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.agile.list_epics.execute(board=args.board, project=args.project or "", include_done=args.done)
    rows = [epic_json(epic) for epic in result.epics]
    document = {**_board_header(result.board), "count": len(rows), "epics": rows}
    print_list(ctx, args, payload=document, rows=rows, list_key="epics", columns=EPIC_COLUMNS, plain=lambda: epics_text(result))
    return 0


# -- writes --------------------------------------------------------------------------------------


def _issues(count: int) -> str:
    return f"{count} issue" + ("" if count == 1 else "s")


def move_text(result: MoveResult, where: str, kind: str) -> str:
    """The text of a move to a sprint or an epic: what would happen, or what happened and what the read-back found."""
    if result.dry_run:
        lines = [f"Dry run: would move {_issues(len(result.keys))} to {where}; nothing was sent ({result.requests} request(s))"]
    else:
        lines = [f"Moved {_issues(len(result.keys))} to {where}."]
    if result.keys:
        lines.append("  " + ", ".join(result.keys))
    if result.skipped:
        lines.append(f"Already there, not sent: {', '.join(result.skipped)}")
    lines += [f"Note: {note}" for note in result.notes]
    if result.dry_run:
        lines.append(pretty(result.payload))
    elif result.read_back:
        confirmed, missing = result.confirmed or [], result.missing or []
        found = f"Read back: {len(confirmed)} of {len(result.keys)} are in the {kind}"
        lines.append(found + (f"; not found there: {', '.join(missing)}" if missing else ""))
    return "\n".join(lines)


def _move_json(result: MoveResult, head: dict[str, Any]) -> dict[str, Any]:
    return {
        **head,
        "keys": result.keys,
        "skipped": result.skipped,
        "payload": result.payload,
        "requests": result.requests,
        "dryRun": result.dry_run,
        "confirmed": result.confirmed,
        "missing": result.missing,
        "notes": result.notes,
        "readBack": result.read_back,
    }


def _warn_missing(ctx: Context, result: MoveResult, kind: str) -> None:
    if result.missing:
        check = "jira sprint ID --issues" if kind == "sprint" else "jira search --under EPIC_KEY"
        ctx.diagnostics.warn(f"Jira accepted the move but {', '.join(result.missing)} were not found in the {kind} afterwards: check with `{check}`.")


def run_sprint_add(ctx: Context, args: argparse.Namespace) -> int:
    result: SprintMoveResult = ctx.app.agile.add_to_sprint.execute(
        args.keys,
        args.sprint,
        board=args.board,
        project=args.project or "",
        if_state=args.if_state or "",
        if_unchanged_since=args.if_unchanged_since or "",
        dry_run=args.dry_run,
    )
    if ctx.json_mode:
        ctx.print_json(_move_json(result, {"sprint": sprint_json(result.sprint)}))
        return 0
    ctx.print(move_text(result, f"sprint {result.sprint.id} '{result.sprint.name}' ({result.sprint.state})", "sprint"))
    _warn_missing(ctx, result, "sprint")
    return 0


def run_epic_add(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.agile.add_to_epic.execute(args.keys, args.epic, if_unchanged_since=args.if_unchanged_since or "", dry_run=args.dry_run)
    if ctx.json_mode:
        ctx.print_json(_move_json(result, {"epic": result.target, "epicName": result.name}))
        return 0
    where = "no epic" if result.target == "none" else f"epic {result.target} '{result.name}'"
    ctx.print(move_text(result, where, "epic"))
    _warn_missing(ctx, result, "epic")
    return 0


def rank_text(result: RankResult) -> str:
    where = f"{result.position} {result.anchor}"
    if result.dry_run:
        return f"Dry run: would rank {', '.join(result.keys)} {where}; nothing was sent\n{pretty(result.payload)}"
    lines = [f"Ranked {', '.join(result.keys)} {where}."]
    if result.read_back:
        verdict = "as asked" if result.confirmed else "NOT as asked"
        lines.append(f"Read back, in the order of the rank: {', '.join(result.order or []) or 'none of them'} ({verdict})")
    return "\n".join(lines + [f"Note: {note}" for note in result.notes])


def run_rank(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.agile.rank_issues.execute(
        args.keys, before=args.before or "", after=args.after or "", if_unchanged_since=args.if_unchanged_since or "", dry_run=args.dry_run
    )
    ctx.print_json(result) if ctx.json_mode else ctx.print(rank_text(result))
    return 0


def sprint_write_text(result: SprintWrite, would: str, did: str) -> str:
    """What a create or an update of a sprint would do (dry run), or did: the sprint as Jira holds it afterwards."""
    if result.dry_run or result.sprint is None:
        target = f" {result.before.id} '{result.before.name}'" if result.before else ""
        lines = [f"Dry run: would {would} the sprint{target}; nothing was sent", pretty(result.payload)]
    elif result.read_back:
        lines = [f"{did} the sprint; read back from Jira:", sprint_text(result.sprint)]
    else:
        lines = [f"{did} the sprint; it could not be read back, so this is what Jira answered to the request:", sprint_text(result.sprint)]
    return "\n".join(lines + [f"Note: {note}" for note in result.notes])


def run_sprint_create(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.agile.create_sprint.execute(
        args.name, args.board, goal=args.goal or "", start=args.start or "", end=args.end or "", dry_run=args.dry_run
    )
    ctx.print_json(result) if ctx.json_mode else ctx.print(sprint_write_text(result, "create", "Created"))
    return 0


def run_sprint_update(ctx: Context, args: argparse.Namespace) -> int:
    if args.no_goal and args.goal is not None:
        raise UsageError("--goal and --no-goal cannot be combined.", hint="Give a new goal with --goal, or remove it with --no-goal.")
    result = ctx.app.agile.update_sprint.execute(
        args.sprint,
        board=args.board,
        project=args.project or "",
        name=args.name,
        goal="" if args.no_goal else args.goal,
        start=args.start or "",
        end=args.end or "",
        state=args.state or "",
        if_state=args.if_state or "",
        dry_run=args.dry_run,
    )
    ctx.print_json(result) if ctx.json_mode else ctx.print(sprint_write_text(result, "change", "Changed"))
    return 0


SPRINT_JSON = "{id, name, state, startDate, endDate, completeDate, goal, boardId}"

COMMANDS: tuple[Command, ...] = (
    Command(
        name="boards",
        group=GROUP,
        summary="List the boards of Jira Software",
        when="Use to find the id of a board, or to see which boards a project has. The other agile commands take the board from the project "
        "when it has one scrum board and ask for --board when it has several.",
        description="Lists the boards you may see, each with the projects it is built on: id, name, type (scrum, kanban, simple).",
        args=(
            Arg(("--project",), "Only boards that are relevant to this project (key).", metavar="KEY"),
            Arg(("--type",), "Only boards of this type.", kind="choice", choices=BOARD_TYPES),
            Arg(("--name",), "Only boards whose name holds this text.", metavar="TEXT"),
            Arg(("--limit",), "Boards to show.", kind="int", default=50, metavar="N"),
            SELECT,
            FORMAT,
        ),
        examples=(Example("jira boards --project PROJ"), Example("jira boards --type scrum --json")),
        json_output=True,
        output="total, count, boards[{id, name, type, projects[]}].",
        handler=run_boards,
    ),
    Command(
        name="sprints",
        group=GROUP,
        summary="List the sprints of a board",
        when="Use to see the sprints of a board with their dates and goal: the active one, the next ones, the last closed ones. For the "
        "numbers of one sprint ask `sprint` for its review, for the pace of the team use `velocity`.",
        description="Active sprints first, then the future ones, then the closed ones, the newest first. A kanban board has no sprints.",
        args=(
            BOARD,
            PROJECT,
            Arg(("--state",), "Only sprints in these states: future, active, closed; several with commas.", metavar="STATE"),
            Arg(("--limit",), "Sprints to show.", kind="int", default=50, metavar="N"),
            SELECT,
            FORMAT,
        ),
        examples=(
            Example("jira sprints --project PROJ --state active,future"),
            Example("jira sprints --board 1200 --state closed --limit 5 --json"),
        ),
        json_output=True,
        output=f"boardId, boardName, total, count, sprints[{SPRINT_JSON}].",
        handler=run_sprints,
    ),
    Command(
        name="sprint",
        group=GROUP,
        summary="Show a sprint, its issues or its review",
        when="Use to read one sprint by id or name: dates and goal; --issues lists what is in it; --review says what was committed, completed, "
        "added, removed and carried over, without raw API calls. It reads only: `sprint-update` changes a sprint, `sprint-add` moves issues.",
        description="A name is looked up among the sprints of the board (exact, case does not matter; an unknown or ambiguous name lists the "
        "candidates). The review is derived from the change history of the issues. People are never shown.",
        args=(
            Arg(("sprint",), "Sprint id, or its name (the words need no quotes).", kind="words", required=True, metavar="SPRINT"),
            BOARD,
            PROJECT,
            Arg(("--issues",), "List the issues in the sprint: key, type, status, points.", kind="flag"),
            Arg(("--review",), "The review: issues and story points committed, completed, added after the start, removed, carried over.", kind="flag"),
            Arg(("--with-subtasks",), "With --review: count sub-tasks too (they are left out by default).", kind="flag"),
        ),
        examples=(
            Example("jira sprint 12 --review --json"),
            Example("jira sprint 'Sprint 12' --board 1200 --issues"),
            Example("jira sprint Sprint 12"),
        ),
        notes=(
            "The review is derived from the history of the field Sprint and of the status, not from Jira's own sprint report, so its numbers "
            "can differ. Story points are the current values. A sprint that has not started has nothing to review; an active one is measured "
            "as of now.",
            "When the history is not whole (Jira refused the search for the issues that left the sprint, or cut the history of an issue), "
            "historyComplete is false and the table marks committed, added and removed with *: they are then lower bounds or may be wrong.",
        ),
        json_output=True,
        output=f"sprint{SPRINT_JSON}, issues (null, or [{{key, issueType, status{{name,category}}, storyPoints, epicKey, summary}}] with "
        "--issues), review (null, or {start, end, ended, committed, completed, added, removed, carriedOver: each {issues, points, "
        "withoutPoints, keys[]}, excludedSubtasks, notes[], historyComplete} with --review).",
        handler=run_sprint,
    ),
    Command(
        name="velocity",
        group=GROUP,
        summary="Committed and completed points per closed sprint",
        when="Use to see how much a team commits to and completes: the last closed sprints of a board, the newest first, and the average of "
        "the completed points. Each sprint is reviewed from its change history, so a long list takes a while.",
        description="Committed is what was in the sprint when it started, completed what was done when it ended (see the review of `sprint`). "
        "Story points are the current values of the issues.",
        args=(
            BOARD,
            PROJECT,
            Arg(("--last",), "How many closed sprints (1 to 20).", kind="int", default=5, metavar="N"),
            SELECT,
            FORMAT,
        ),
        examples=(Example("jira velocity --project PROJ"), Example("jira velocity --board 1200 --last 8 --json")),
        notes=(
            "A sprint whose history is not whole has historyComplete false and a * on its committed numbers in the table: they are lower "
            "bounds or may be wrong. A closed sprint that cannot be reviewed is left out and its id is in leftOut.",
        ),
        json_output=True,
        output="boardId, boardName, count, averageCompleted, sprints[{id, name, startDate, completeDate, committedIssues, committedPoints, "
        "completedIssues, completedPoints, historyComplete}], leftOut[] (ids of closed sprints that could not be reviewed), notes[].",
        handler=run_velocity,
    ),
    Command(
        name="backlog",
        group=GROUP,
        summary="List the backlog of a board in rank order",
        when="Use to see what is waiting on a board, in the order of the rank, before you plan a sprint or rank issues. To read one issue "
        "use `issue`, to find issues by words use `search`.",
        description="The issues of the board's backlog, compact and without people.",
        args=(BOARD, PROJECT, Arg(("--limit",), "Issues to show, from the top of the rank.", kind="int", default=50, metavar="N"), SELECT, FORMAT),
        examples=(Example("jira backlog --project PROJ --limit 20"), Example("jira backlog --board 1200 --json")),
        json_output=True,
        output="boardId, boardName, total, count, issues[{key, issueType, status{name,category}, storyPoints, epicKey, summary}].",
        handler=run_backlog,
    ),
    Command(
        name="epics",
        group=GROUP,
        summary="List the epics of a board",
        when="Use to see the epics of a board and whether they are done. To list the stories of an epic use `search` with --under KEY.",
        description="Epics that are not done; with --done the finished ones too.",
        args=(BOARD, PROJECT, Arg(("--done",), "Include the epics that are done.", kind="flag"), SELECT, FORMAT),
        examples=(Example("jira epics --project PROJ"), Example("jira epics --board 1200 --done --json")),
        json_output=True,
        output="boardId, boardName, count, epics[{key, name, summary, done}].",
        handler=run_epics,
    ),
    Command(
        name="sprint-add",
        group=GROUP,
        summary="Move issues into a sprint",
        when="Use to move issues into a future or active sprint after the person asked for it. Dry-run first; adding to an active sprint "
        "changes its scope, so pass --if-state future when that must not happen. Issues leave the other open sprint they are in.",
        description="Reads the sprint and every issue first (an unknown key or sprint fails before anything is sent), sends the issues in "
        "batches of 50, and reads the sprint back. Issues that are in the sprint already are not sent.",
        args=(
            Arg(("keys",), "Issue keys, separated by spaces or commas.", kind="words", required=True, metavar="ISSUE_KEY"),
            Arg(("--sprint",), "The sprint: its id, or its name (exact, case does not matter).", required=True, metavar="ID|NAME"),
            BOARD,
            PROJECT,
            IF_STATE,
            SINCE,
            DRY_RUN,
        ),
        examples=(
            Example("jira sprint-add PROJ-1 PROJ-2 --sprint 12 --dry-run"),
            Example("jira sprint-add PROJ-1,PROJ-2 --sprint 'Sprint 12' --board 1200 --if-state future"),
        ),
        writes=True,
        json_output=True,
        output=f"sprint{SPRINT_JSON}, keys[], skipped[], payload{{issues[]}} (sent in requests of at most 50), requests, dryRun, "
        "confirmed[] and missing[] (what the read-back found; null when nothing was read back), notes[], readBack (false for a dry run, when "
        "there was nothing to send, and when the read-back failed after the move was made).",
        handler=run_sprint_add,
    ),
    Command(
        name="rank",
        group=GROUP,
        summary="Rank issues before or after another issue",
        when="Use to put issues above or below another issue in the rank, which orders the backlog and the board. Dry-run first. At most 50 "
        "issues; they keep the order you give them.",
        description="Reads every issue first, ranks them with one call, and reads the order back to check that they stand where you asked.",
        args=(
            Arg(("keys",), "Issue keys, separated by spaces or commas.", kind="words", required=True, metavar="ISSUE_KEY"),
            Arg(("--before",), "Put the issues just above this issue.", metavar="ISSUE_KEY"),
            Arg(("--after",), "Put the issues just below this issue.", metavar="ISSUE_KEY"),
            SINCE,
            DRY_RUN,
        ),
        examples=(Example("jira rank PROJ-4 PROJ-3 --before PROJ-1 --dry-run"), Example("jira rank PROJ-7 --after PROJ-2")),
        writes=True,
        json_output=True,
        output="keys[], position (before or after), anchor, payload, dryRun, order[] (read back, by rank) and confirmed (true or false), "
        "both null when the order was not read, notes[], readBack (false for a dry run and when the read-back failed after the rank was made).",
        handler=run_rank,
    ),
    Command(
        name="epic-add",
        group=GROUP,
        summary="Move issues into an epic, or out of their epic",
        when="Use to put issues into an epic after the person asked for it, or with --epic none to take them out of their epic. Dry-run first. "
        "An issue is in one epic only: it leaves the one it was in.",
        description="Reads the epic and every issue first, sends the issues in batches of 50, and reads the epic back. Issues that are there already "
        "are not sent. Epics cannot be put into an epic.",
        args=(
            Arg(("keys",), "Issue keys, separated by spaces or commas.", kind="words", required=True, metavar="ISSUE_KEY"),
            Arg(("--epic",), "Key of the epic, or `none` to take the issues out of their epic.", required=True, metavar="EPIC_KEY|none"),
            SINCE,
            DRY_RUN,
        ),
        examples=(Example("jira epic-add PROJ-1 PROJ-2 --epic PROJ-9 --dry-run"), Example("jira epic-add PROJ-4 --epic none")),
        writes=True,
        json_output=True,
        output="epic (key, or none), epicName, keys[], skipped[], payload{issues[]} (sent in requests of at most 50), requests, dryRun, "
        "confirmed[] and missing[] (what the read-back found; null when nothing was read back), notes[], readBack (false for a dry run, when "
        "there was nothing to send, and when the read-back failed after the move was made).",
        handler=run_epic_add,
    ),
    Command(
        name="sprint-create",
        group=GROUP,
        summary="Create a future sprint",
        when="Use to create the next sprint of a scrum board after the person asked for it. Dry-run first. It never starts the sprint: "
        "`sprint-update` does that.",
        description="The name is trimmed; a future or active sprint with the same name on the board is refused as a duplicate. Dates are optional "
        "(2026-10-12, 2026-10-12T09:00 or with an offset; without one they count as UTC).",
        args=(
            Arg(("name",), "The name of the sprint (the words need no quotes).", kind="words", required=True, metavar="NAME"),
            Arg(("--board",), "Id of the scrum board that the sprint belongs to.", kind="int", required=True, metavar="ID"),
            Arg(("--goal",), "The goal of the sprint.", metavar="TEXT"),
            Arg(("--start",), "Planned start, ISO 8601.", metavar="ISO"),
            Arg(("--end",), "Planned end, ISO 8601.", metavar="ISO"),
            DRY_RUN,
        ),
        examples=(
            Example("jira sprint-create Sprint 14 --board 1200 --dry-run"),
            Example("jira sprint-create 'Sprint 14' --board 1200 --goal 'Ship the export' --start 2026-11-02 --end 2026-11-16"),
        ),
        writes=True,
        json_output=True,
        output=f"sprint ({SPRINT_JSON} as Jira holds it; null for a dry run), before (null), payload, dryRun, notes[], readBack (false: the "
        "sprint was created but could not be read back, and sprint is what Jira answered to the creation).",
        handler=run_sprint_create,
    ),
    Command(
        name="sprint-update",
        group=GROUP,
        summary="Rename, re-date, start or complete a sprint",
        when="Use to change a sprint's name, goal or dates, to start it (--state active) or to complete it (--state closed), after the person "
        "asked for it. Dry-run first. Starting and completing change the team's work: guard them with --if-state.",
        description="A partial update: only what you pass changes; --no-goal removes the goal. Jira's rules are checked first: a closed sprint "
        "cannot be changed, a sprint is started from future with a start and an end date (--start now is allowed), completed from active; "
        "Jira sets the complete date. Completing does not move the open issues.",
        args=(
            Arg(("sprint",), "Sprint id, or its name (the words need no quotes).", kind="words", required=True, metavar="SPRINT"),
            BOARD,
            PROJECT,
            Arg(("--name",), "New name.", metavar="TEXT"),
            Arg(("--goal",), "New goal.", metavar="TEXT"),
            Arg(("--no-goal",), "Remove the goal (an empty --goal does the same, but Windows PowerShell 5.1 drops an empty argument).", kind="flag"),
            Arg(("--start",), "Start date, ISO 8601, or `now`.", metavar="ISO"),
            Arg(("--end",), "End date, ISO 8601.", metavar="ISO"),
            Arg(("--state",), "`active` starts the sprint, `closed` completes it.", kind="choice", choices=("active", "closed")),
            IF_STATE,
            DRY_RUN,
        ),
        examples=(
            Example("jira sprint-update 12 --state active --start now --end 2026-11-16 --if-state future --dry-run"),
            Example("jira sprint-update 'Sprint 12' --board 1200 --goal 'New goal'"),
        ),
        writes=True,
        json_output=True,
        output=f"sprint ({SPRINT_JSON} as Jira holds it after the change; null for a dry run), before (as it was), payload, dryRun, notes[], "
        "readBack (false: the sprint was changed but could not be read back, and sprint is what Jira answered to the change).",
        handler=run_sprint_update,
    ),
)
