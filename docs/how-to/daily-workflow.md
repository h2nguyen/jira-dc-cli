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
jira issue PROJ-1 PROJ-2 PROJ-3 --only summary,status   # several issues with one request, only the parts you need
jira issue PROJ-123 --outline            # the headings of a long description; --section Steps reads one part
jira issue PROJ-123 --history            # when the status changed
jira filters                             # your saved filters
jira issues --filter 10000               # run one (--jql narrows it)
```

`jira issues` lists the current project (see `jira use`) when you give no `--project` or `--jql`. Be careful with `--jql`: it **replaces** the
project condition, so a query without `project = ...` searches every project (the CLI says so on stderr). A query with double quotes goes into a file
(`--jql '@query.jql'`, at most 1 MiB), which survives Windows PowerShell 5.1; a search text that starts with `@` is read as a file name, so put a space in front of
it or use `--phrase`. A key that does not exist has its own entry and the exit code is 4; the other issues are still
printed. More: [read issues in batches and in parts](read-issues.md).

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

When the screen of a transition asks for fields (a resolution, a custom field), list them, then send them; the dry run checks the values against the screen:

```bash
jira transition PROJ-123 --to Done --show-fields                  # what the screen asks for, with the allowed values
jira transition PROJ-123 --to Done --resolution Fixed --dry-run
jira transition PROJ-123 --to Done --fields-json '@fields.json' --if-unchanged-since 2026-10-15T08:00:00Z
```

## Talk about it

```bash
jira comment PROJ-123 --text "Fixed in the next build."
jira comment PROJ-123 --file notes.md --wiki      # Markdown becomes Jira wiki markup first
jira comment PROJ-123 --text "For developers." --visible-to role:Developers
jira comment PROJ-123 --list                      # read them; authors are hidden unless --with-users
jira comment PROJ-123 --list --since 2d --last 3  # the newest three of the last two days
jira comment PROJ-123 --get 10100                 # one comment; --rendered shows it as Jira renders it
jira comment PROJ-123 --edit 10100 --file new.md --wiki --dry-run   # correct a comment: the dry run shows a diff
```

`--edit` replaces the text of a comment and keeps who may see it; a comment is never deleted. Read it with `--get`, dry-run the edit, and give the `updated` time you read as
`--if-unchanged-since`. A comment of somebody else needs the permission to edit all comments (exit code 5). See [comments, people and checked values](comments-and-people.md).

The file options (`--file`, `--comment-file`, `--description-file`) and the JSON file arguments read standard input
when you give `-`: `git log -5 --oneline | jira comment PROJ-123 --file -`.

## Attachments

```bash
jira attachments PROJ-123                                  # the files: id, name, type, size, time
jira download PROJ-123 --all --dry-run                     # what would be written; nothing is fetched
jira download PROJ-123 10001 --out tmp/shots               # save one file
jira download PROJ-123 --name 'error log.txt' --text --max-chars 3000   # read a text file, a docx, xlsx or pptx
jira attach PROJ-123 shot.png --comment 'Screenshot of the failure.' --dry-run
jira attach PROJ-123 'my report 2024.pdf' --dry-run              # a path with blanks goes in quotes; --path FILE is the exact form
```

Names are made safe and nothing is overwritten without `--force`, not even by a file that appears while the download runs. If a `download --all` fails after some files were written, the error lists them (`details.written`) and a new run needs `--force` or an ID. The token goes to your Jira only: `attach` refuses the configuration of the CLI and any file that holds the token, and `download` never writes into the folder that holds the configuration. No command deletes an attachment, and `attach` cannot take one back: attach nothing
with secrets or personal data. See [attachments](attachments.md).

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
A text that you copied from `jira issue` and that still holds `[~user]`, `[email]` or `[personal link]` is refused by `update`, `update-json` and `transition --fields-json` (exit code 1, nothing is sent): they stand in place of people and would overwrite the real mention. Write the text again without them.
`--if-unchanged-since` refuses to write (exit code 3) when someone changed the issue after that time; use the
`fetchedAt` of an export or the time you read the issue. It is a check right before the write, not a lock: Jira has
no atomic compare-and-set, so a change in the few milliseconds between the check and the write is not caught.

## Assign

```bash
jira users resolve 'Jane Doe' --issue PROJ-123     # the user name for --assignee: matches, name, active; no people printed
jira assign PROJ-123 'Jane Doe' --dry-run          # also me, unassigned, automatic; without --dry-run it assigns
jira update PROJ-123 --assignee 'Jane Doe' --dry-run   # the payload shows the user name it resolved to
```

A display name or an address works when it fits exactly one user; a name that fits several users is refused with their number, and one that fits nobody is exit code 4.
See [comments, people and checked values](comments-and-people.md#4-people-find-the-user-name-and-assign).

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

## Plan and review a sprint

```bash
jira boards --project PROJ                       # the board id
jira sprints --project PROJ --state active,future
jira sprint 12 --issues                          # what is in the sprint
jira sprint 12 --review                          # committed, completed, added, removed, carried over
jira velocity --project PROJ --last 5            # committed and completed points of the last closed sprints
jira backlog --project PROJ --limit 20           # the top of the backlog, in rank order
jira epics --project PROJ                        # the epics that are not done
```

The review is derived from the change history of the issues, not from Jira's own sprint report, and says so in `notes`. The writes have `--dry-run` and read
their targets first:

```bash
jira sprint-add PROJ-24 PROJ-22 --sprint 'Sprint 13' --if-state future --dry-run
jira rank PROJ-7 PROJ-8 --after PROJ-2 --dry-run
jira epic-add PROJ-4 PROJ-5 --epic PROJ-9 --dry-run              # --epic none takes them out of their epic
jira sprint-create 'Sprint 14' --board 1200 --goal 'Ship the export' --start 2026-11-02 --end 2026-11-16 --dry-run
jira sprint-update 'Sprint 14' --board 1200 --state active --if-state future --dry-run
```

`--if-state future` stops a move into a sprint that has started meanwhile (exit code 3). A real run reads the sprint, the epic or the rank back: `readBack: true`. When that read fails the write still counts (exit code 0, `readBack: false`, `confirmed` and `missing` are `null`, a note says so): look with `jira sprint 12 --issues` and do not repeat a `sprint-create`. See [sprints and boards](sprints-and-boards.md).

## Watch, vote, log work, add a web link

```bash
jira permissions --issue PROJ-123 --only WORK_ON_ISSUES        # may I?
jira watch PROJ-123 --dry-run                                  # without --dry-run you watch; --remove stops (your own watch only)
jira vote PROJ-123 --dry-run                                   # your own vote only
jira log-work PROJ-123 '1h 30m' --comment 'Reviewed the export.' --dry-run
jira worklogs PROJ-123 --since 7d                              # the time logged, newest first, with the total
jira remote-link PROJ-123 --url 'https://wiki.example.com/runbook' --title 'Runbook' --dry-run
jira remote-links PROJ-123
```

Everyone sees a vote, a worklog and a web link: run each once, and read what it printed. A result with `confirmed: null` (for `vote`: `count` and `hasVoted` are `null`) and a `message` means that the write was made and only its read-back failed: look with `jira worklogs PROJ-123` (or `remote-links`, `watchers`, `votes`) and do not run it again. See [collaborate on an issue](collaborate-and-reference.md).

## Look up what your Jira calls things

```bash
jira statuses --project PROJ                    # the statuses per issue type; the last line says what "done" means
jira priorities                                 # also resolutions, issue-types, components, roles, dashboards
jira fix-versions PROJ --unreleased --counts    # the versions that are not released, with the issues in each
jira server                                     # the Jira version, and what is switched on (attachments, voting, time tracking)
```

## Preview first, always

Every command that changes Jira has `--dry-run`. It prints the request that would be sent, checks that the
issue exists, and sends nothing. A routine that never hurts: dry run, read the payload, run for real. The dry run of `create`, `update`, `transition` and the imports also checks the
priority, the components, the fix versions and the fields of a transition against what the project offers: a value that it does not offer is exit code 6, with the allowed values in the hint.

## Keep Markdown tickets and Jira in step

```bash
jira diff docs/tickets                         # who changed what, field by field (reads only)
jira pull docs/tickets --update --dry-run      # what Jira changed since the last sync
jira pull docs/tickets --update                # bring it in; your own edits stay
jira push docs/tickets/PROJ-12-fix.md --dry-run   # what your edit would send
```

`pull` writes files, `push` changes Jira (after a `--dry-run`, one issue per command), and a field that was changed on both sides is a conflict that you decide
(exit code 3). The links between issues are one line of the file, `links: Blocks -> PROJ-2, Blocks <- PROJ-3`: `push` creates the links that only the file
lists and never removes one. The rules are in [export and sync](export-and-sync.md#keep-a-folder-of-markdown-tickets-in-step-with-jira).

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
