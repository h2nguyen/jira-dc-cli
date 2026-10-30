"""Commands of the reading area: the saved filters. Issues are read by the commands in `issues.py`."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from ..listing import FORMAT, SELECT, print_list
from ..render import key_values, one_line, table, to_jsonable
from ..runtime import Context
from ..spec import Arg, Command, Example

if TYPE_CHECKING:
    from .....domain.savedfilters import SavedFilter

GROUP = "Read"
WITH_USERS = Arg(("--with-users",), "Show the owner of the filter and the people that its JQL names. Refused when JIRA_HIDE_USERS=true.", kind="flag")


def filter_row(item: SavedFilter, *, detail: bool = False) -> dict[str, Any]:
    """A filter in the shape of its JSON. The owner is `null` unless `--with-users` (the use case then keeps it)."""
    row: dict[str, Any] = {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "jql": item.jql,
        "favourite": item.favourite,
        "owner": to_jsonable(item.owner),
    }
    if detail:
        row["searchUrl"] = item.search_url
        row["viewUrl"] = item.view_url
    return row


def run_filters(ctx: Context, args: argparse.Namespace) -> int:
    found = ctx.app.reading.list_filters.execute(with_users=args.with_users)
    rows = [filter_row(item) for item in found]
    columns = [("ID", "id"), ("NAME", "name")] + ([("OWNER", "owner.displayName")] if args.with_users else []) + [("JQL", "jql")]

    def plain() -> str:
        cells = []
        for item in found:
            row = [item.id, one_line(item.name)]
            if args.with_users:
                row.append(one_line(item.owner.display_name if item.owner else ""))
            cells.append((*row, one_line(item.jql)))
        return table([header for header, _ in columns], cells)

    print_list(ctx, args, payload=rows, rows=rows, list_key=None, columns=columns, plain=plain)
    if not found:
        ctx.diagnostics.info("You have no favourite filters. Star a filter in Jira, or read one by its id with `jira filter ID`.")
    hidden = sum(item.people_hidden for item in found)
    if hidden:
        ctx.diagnostics.info(
            f"The JQL of {hidden} {'filter' if hidden == 1 else 'filters'} names people or personal data: "
            "[user] or [email] stands in their place. `jira issues --filter ID` still runs a filter as it is."
        )
    return 0


def filter_text(item: SavedFilter) -> str:
    left_out = "(left out: the JQL names people)" if item.people_hidden else ""
    pairs = [
        ("Favourite", "yes" if item.favourite else "no"),
        ("Description", one_line(item.description)),
        ("JQL", one_line(item.jql)),
        ("Owner", item.owner.display_name if item.owner else ""),
        ("Search URL", item.search_url or left_out),
        ("Open", item.view_url),
        ("Run it", f"jira issues --filter {item.id}"),
    ]
    return "\n".join([f"{item.id}  {one_line(item.name)}", key_values(pairs)])


def run_filter(ctx: Context, args: argparse.Namespace) -> int:
    item = ctx.app.reading.show_filter.execute(args.id, with_users=args.with_users)
    ctx.print_json(filter_row(item, detail=True)) if ctx.json_mode else ctx.print(filter_text(item))
    if item.people_hidden:
        ctx.diagnostics.info(
            "The JQL names people or personal data: [user] or [email] stands in their place, and the search address is left out. "
            f"`jira issues --filter {item.id}` still runs the filter as it is."
        )
    return 0


COMMANDS: tuple[Command, ...] = (
    Command(
        name="filters",
        group=GROUP,
        summary="List your favourite saved filters",
        when="Use to find the id of a saved filter, so that `issues --filter ID` or `search --filter ID` runs it, and to see the JQL that it holds. "
        "Jira Data Center can list only your favourite filters, not every filter: for another one use `filter ID`.",
        description="Lists the saved filters that you marked as favourite: id, name and JQL. The owner, and the people that a JQL names, are shown "
        "only with --with-users: without it `assignee = jdoe` reads `assignee = [user]`.",
        args=(WITH_USERS, SELECT, FORMAT),
        examples=(
            Example("jira filters"),
            Example("jira filters --json --select id,name,jql", "only these keys of every filter"),
            Example("jira filters --format md"),
        ),
        notes=(
            "Jira Data Center has no call that lists every filter: this lists the favourite filters of the user of the token. To read another "
            "filter give its id, the number in the address of the filter in the browser (filter=10000), to `jira filter`.",
            "Without --with-users the user names that a JQL holds after assignee, reporter, creator, watcher, voter, commentedBy and the like are "
            "[user]; currentUser(), membersOf(\"group\") and EMPTY stay. A user field of its own (cf[10100] = jdoe) is not recognised and stays.",
            "Filters are only read here. To create or change one use `jira api` (a filter is not deleted by this CLI).",
        ),
        json_output=True,
        output="a list of {id, name, description, jql, favourite, owner (null unless --with-users)}; with --select only those (dotted) keys. "
        "The jql has [user] for the people that it names unless --with-users.",
        handler=run_filters,
    ),
    Command(
        name="filter",
        group=GROUP,
        summary="Show one saved filter",
        when="Use to see what a saved filter holds, its JQL, before you run it with `issues --filter ID` or `search --filter ID`, and to get the "
        "address that opens it in the browser. It reads only.",
        description="Shows the name, description and JQL of a saved filter, the REST address that runs it and the address that opens it in the "
        "browser. The owner, and the people that the JQL names, are shown only with --with-users; the REST address is left out when the JQL "
        "names people.",
        args=(
            Arg(("id",), "The id of the filter: the number in the address of the filter in the browser (filter=10000).", required=True, metavar="FILTER_ID"),
            WITH_USERS,
        ),
        examples=(Example("jira filter 10000"), Example("jira filter 10000 --json")),
        json_output=True,
        output="{id, name, description, jql ([user] for the people that it names unless --with-users), favourite, owner (null unless --with-users), "
        'searchUrl ("" when the JQL names people), viewUrl}.',
        handler=run_filter,
    ),
)
