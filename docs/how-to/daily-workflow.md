# Daily workflow

Recipes for the work you do every day. Every command has `jira COMMAND --help` with its options and
examples; the [command reference](../reference/commands.md) has the same text.

## Find your work

```bash
jira issues --mine                       # your unresolved issues, most recently updated first
jira issues --jql 'project = PROJ AND status = "In Review" ORDER BY updated DESC'
jira issues --limit 20 --start-at 20     # the next page; or --all for every page
jira issue PROJ-123                      # one issue: fields, epic, points, sprint, links, acceptance criteria
jira issue PROJ-123 --comments           # and its comments
```

`jira issues` lists the current project (see `jira use`) when you give no `--project` or `--jql`. Be careful with `--jql`: it **replaces** the
project condition, so a query without `project = ...` searches every project (the CLI says so on stderr).

## Find what the team wrote down

```bash
jira search "how do I export the monthly report?" --snippets 3     # a whole question; the passage around your words for the first three hits
jira search audit trail --status "To Do" --type Story            # words and filters
jira search --under PROJ-5                                        # the stories of an epic
jira search roles --since 7d --all-projects --limit 20
```

`jira search` leaves out filler words and says which, loosens the search when few issues hold every word, and works in the current project unless you add
`--project` or `--all-projects`. [Find information in Jira](find-information.md) explains the rules and the loop an AI agent follows.

## Start working

```bash
git switch -c "$(jira branch PROJ-123)"          # feature/PROJ-123-summary-as-a-slug (bugfix/ for a Bug)
jira transition PROJ-123                         # which moves are possible from the current status?
jira transition PROJ-123 --to "In Progress"
```

`--to` accepts the target status, the transition name or the transition id. The status wins because the
name of a transition can differ from the status it leads to (`Reopen` leads to `To Do`). When two
transitions lead to the same status the CLI refuses and lists the candidates, and you pick one by its id.

A comment can travel with the move: `jira transition PROJ-123 --to "In Review" --comment "Ready for review."`.

## Talk about it

```bash
jira comment PROJ-123 --text "Fixed in the next build."
jira comment PROJ-123 --file notes.md --wiki      # Markdown becomes Jira wiki markup first
jira comment PROJ-123 --list                      # read them; authors are hidden unless --with-users
```

The file options (`--file`, `--comment-file`, `--description-file`) and the JSON file arguments read standard input
when you give `-`: `git log -5 --oneline | jira comment PROJ-123 --file -`.

## Relate issues

```bash
jira link-types                                       # the exact names and their phrases
jira link PROJ-1 --type Blocks --to PROJ-2            # PROJ-1 blocks PROJ-2
jira link PROJ-1 --type Blocks --to PROJ-2 --inverse  # PROJ-1 is blocked by PROJ-2 ("depends on")
jira link PROJ-1 --type Blocks --to PROJ-2 --dry-run  # says the relation in words, sends nothing
```

The link type is the exact name; phrases such as "requires" occur in several link types and are refused.
After a link is created the relation is read back from Jira and printed, so a wrong direction is visible at once.

## Change fields

```bash
jira update PROJ-123 --summary "Clearer title" --labels backend,api
jira update PROJ-123 --description-file body.md --wiki --dry-run
jira update PROJ-123 --priority High --if-unchanged-since 2026-10-15T08:00:00Z
jira update PROJ-123 --fields-json '{"customfield_10001": 5}'
jira update-json PROJ-123 payload.json          # a complete Jira payload, e.g. {"update": {"labels": [{"add": "x"}]}}
```

Lists replace the current values (`--labels ""` clears them). To add one label, use `update-json`.
`--if-unchanged-since` refuses to write (exit code 3) when someone changed the issue after that time; use the
`fetchedAt` of an export or the time you read the issue. It is a check right before the write, not a lock: Jira has
no atomic compare-and-set, so a change in the few milliseconds between the check and the write is not caught.

## Create

```bash
jira create --type Story --summary "Export the monthly report" --labels reporting --dry-run
jira create --type Story --summary "Export the monthly report" --description-file story.md --wiki
```

The issue type is resolved by name against what the project can create. The reporter is `JIRA_REPORTER`, or
you. For many tickets see [import tickets](import-tickets.md).

## See the progress

```bash
jira report --sprint active --epics        # issues and story points by status and by epic
jira report --fix-version "Release 1.0" --json
```

"Done" means the status category done, so it follows your workflow, whatever the status is called.
Epics are not counted themselves.

## Preview first, always

Every command that changes Jira has `--dry-run`. It prints the request that would be sent, checks that the
issue exists, and sends nothing. A routine that never hurts: dry run, read the payload, run for real.

## Keep Markdown tickets and Jira in step

```bash
jira diff docs/tickets                         # who changed what, field by field (reads only)
jira pull docs/tickets --update --dry-run      # what Jira changed since the last sync
jira pull docs/tickets --update                # bring it in; your own edits stay
jira push docs/tickets/PROJ-12-fix.md --dry-run   # what your edit would send
```

`pull` writes files, `push` changes Jira (after a `--dry-run`, one issue per command), and a field that was changed on both sides is a conflict that you decide
(exit code 3). The rules are in [export and sync](export-and-sync.md#keep-a-folder-of-markdown-tickets-in-step-with-jira).

## Use the output in scripts

Add `--json` for stable keys, or `JIRA_OUTPUT=json` for a whole session. There is no jq to install; Python is
there already:

```bash
jira issues --mine --json | python -c "import json,sys; [print(i['key'], i['summary']) for i in json.load(sys.stdin)['issues']]"
for key in $(jira issues --jql 'status = "In Review"' --all --json | python -c "import json,sys; print(*[i['key'] for i in json.load(sys.stdin)['issues']])"); do
  jira comment "$key" --text "Reminder: please review." --dry-run
done
```

Results go to standard output, progress and warnings to standard error, and the exit code says what happened
([exit codes](../reference/exit-codes.md)). The shapes are in the [JSON reference](../reference/json-output.md).

## Anything the commands do not cover

`jira api GET issue/PROJ-1?fields=summary` calls any REST endpoint (write the path without the leading `/`, which Git
Bash would turn into a Windows path; the CLI undoes that, but this avoids it); `POST` and `PUT` need `--write`, and
`--dry-run` previews them. DELETE is not offered.
