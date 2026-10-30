# Export a backlog and keep a mirror in sync

`jira export` reads every issue of a project in one command and writes it as JSON in a stable format
("backlog dump v1"), with the custom fields and **without personal data**. Other tools read that file: a
comparison with a Markdown mirror, a report, a backup, an agent that needs the whole backlog.

```bash
jira export --project PROJ --out tmp/jira-sync/PROJ.json
jira export --project PROJ > PROJ.json          # to standard output
```

- It fetches page after page (the server may cap the page size) and **refuses to write a partial dump**: if Jira
  reports 120 issues and 119 arrived, nothing is written and the exit code is 7.
- It asks only for the fields in the dump: no assignee, no reporter, no comments. Text is redacted for mentions,
  e-mail addresses and personal share links.
- `--out` creates missing folders, writes UTF-8 with plain `\n` line ends and prints how many issues it wrote.
- The custom field ids are resolved by name (or `JIRA_*_FIELD`) and recorded in the dump, so a reader does not
  hard-code them.

The format is documented in the [JSON reference](../reference/json-output.md#the-backlog-dump-v1).

## Write back safely

The dump records when it was fetched (`fetchedAt`). Use it as a guard when you change an issue on the basis of
the dump:

```bash
jira update PROJ-123 --labels reviewed --if-unchanged-since "2026-10-15T08:07:04+00:00" --dry-run
```

If somebody changed the issue after that time the command refuses with exit code 3 and writes nothing; read the
issue again, decide, and retry with a newer time. A bulk write loop should stop on the first exit code 3. The check
happens right before the write and is not a lock (Jira offers no atomic compare-and-set), so it protects against a
stale dump, not against two people writing in the same instant.

## Keep a folder of Markdown tickets in step with Jira

A folder of Markdown tickets and Jira are two copies of the same issues. Three commands work with them:

| Command | Reads | Changes |
| --- | --- | --- |
| `jira diff DIR` | Jira and the files | nothing |
| `jira pull DIR` | Jira | files in `DIR`, never Jira |
| `jira push FILE` | the file and Jira | one issue in Jira, and the sync record of that file |

```bash
jira diff docs/tickets                       # which issues differ, and who changed which field
jira pull docs/tickets --dry-run             # what would be written
jira pull docs/tickets                       # a file for every issue that has none
jira pull docs/tickets --update              # bring in what Jira changed since the last sync
jira push docs/tickets/PROJ-12-fix.md --dry-run    # show the payload, send nothing
jira push docs/tickets/PROJ-12-fix.md              # after the person agreed; push one issue at a time
```

**What is kept in step:** summary, priority, labels, components, fix versions, story points, acceptance criteria, the links between issues ([below](#links-between-issues)) and the description (Jira's wiki markup
becomes Markdown on the way in, and Markdown becomes wiki markup on the way out). **What is not:** the assignee and every other person (they are left out
on purpose), status, sprint, comments, and the issue type and epic (a file gets them when it is made and keeps them): they change too often or belong to people. A file is found by its `issue_key` or `jira_key` line, so
mirrors that name the key differently work too; `jira-sync-ignore.txt` in the folder (one key per line, `#` for comments) leaves issues out of `diff` and `pull`. Three things leave a field out
of the comparison, and `diff` and `pull` say which on standard error (`Not compared ...`) and in `--json` (`skipped`): a file whose `mirror:` line says `sanitized` or whose text holds a relative
link (a link to a document; an address, an anchor, an issue key as the target (Jira's `[the story|PROJ-3]`), an attached image such as `!shot.png!` and anything inside code do not count; a path to a picture does) is kept in step without its description, because that text was changed on purpose;
and a list that holds a name with a comma (a component `Billing, Invoices`) cannot be written as a line of comma separated names, so it is left alone (the links of an issue are left alone in the same way when one of its link types has a comma, `<` or `>` in its name). People's names that Jira holds in a label, a component,
a version or a sprint are redacted like any other text (in `export` too).

**How it knows who changed what.** Every file that was synced carries two lines in its front matter (shortened here):

```text
jira_base: summary=368aa8/368aa8,priority=de4c86/de4c86,labels=2724b5/2724b5,description=3cab18/3cab18,links=3b9130/3b9130
jira_synced: 2026-10-16T21:48:20+00:00
```

`jira_base` records, for each field, a short fingerprint of what the file held and of what Jira held when they last agreed (two, because the wiki
markup and the Markdown of the same text are written differently). With it a field is compared with the last agreement, not only with the other side:

| State in `jira diff` | Meaning | `jira pull --update` | `jira push` |
| --- | --- | --- | --- |
| in step | nothing differs | nothing to do | nothing to send |
| `jira changed` | Jira changed a field, you did not | rewrites that field | nothing to send |
| `edited here` | you edited a field, Jira did not change it | leaves the file alone | sends that field |
| `both sides` | you edited one field and Jira changed another | rewrites Jira's field, keeps your edit | sends your field only, Jira's change stays |
| `conflict` | the same field changed on both sides | changes nothing in that file, exit code 3 | refuses, exit code 3 |
| `differs` | a field differs and nothing was ever agreed (a file that never went through `pull` or `push`) | leaves the file alone | refuses |
| only in Jira / only here / twice | no file for the issue / no issue for the file / two files for one key | creates the file / says so / says so | says so |

`--force` decides a conflict or a `differs` on purpose: with `pull --update --force` Jira's value wins and your edit of that field is lost; with `push --force` the file's
value wins and Jira's change of that field is lost. A field that only one side edited is never touched by the other side's `--force`.

```text
$ jira diff docs/tickets --issue PROJ-1,PROJ-2
PROJ-1, PROJ-2: 2 files, 2 issues in Jira | in step 1 | both sides 1

KEY     STATE       DETAIL                       FILE
PROJ-2  both sides  here: summary; Jira: labels  PROJ-2-fix-the-thing.md
```

**Rules that protect the data:**

- `pull` never deletes a file and, without `--force`, never overwrites an edit of yours; the first `pull` of an existing folder only adds `jira_base` and `jira_synced` to the files
  that are in step (so it changes every such file once: commit that as a change of its own). A file that is rewritten ends up with plain line feeds and without a byte order mark; nothing else
  outside the fields that change is touched. A file that cannot be read, and a key that two files hold, are named in the answer and left alone.
- Blanks and line breaks do not make two texts differ (Markdown and Jira's markup write the same text with different spacing), so an edit of nothing but the spacing is not sent. It still counts as an
  edit: `pull` does not note it as agreed (the outcome is `skipped`, naming the field), and when Jira changes the same text later it is a conflict, never an overwrite; this holds when someone changed the
  spacing in Jira too. An edit of the indentation inside a code block is such an edit. To end it, put the spacing back, or delete the file's `jira_base` line: the next `pull` then notes the file as it is
  (a field that really differs from Jira then counts as never synced, and `push` needs `--force` for it). The other way round, the record cannot tell a change of nothing but the spacing in Jira from a change of
  the words: if you edit a text for real and someone changes its spacing in Jira before your next `pull`, `diff` shows a conflict (nothing is overwritten; `--force` on either side ends it). A `pull` in between notes
  Jira's spacing, and then there is no conflict.
- `push` sends only the fields you edited, never reverts a field that only Jira changed, and checks again just before the write that the issue did not change
  meanwhile (the same guard as `--if-unchanged-since`, with the time it just read). Afterwards it reads the issue back and notes the new state in the file; when Jira holds the change but the file cannot be
  written (another program holds it open), the answer says `pushed` and why, and the next `pull` notes the state. A field it does not compare for that file (a description with a relative link, a list
  with a name that holds a comma, the links of an issue with a link type that the line cannot hold, `mirror: sanitized`) is named in the answer (`not compared: description`), also with `--dry-run` and when Jira changed another field meanwhile, so that `pushed` is not read as
  more than it says; a field that this Jira does not have is not named. It creates no issue (`import-md` does) and deletes nothing: it creates links between issues and never removes one. With several
  files (`--issue KEY,KEY`; the skills push one issue per command) it stops at the first refusal.
- A placeholder such as `[~user]`, `[email]` or `[personal link]` is never sent, not even with `--force`, in a text or in the name of a label, a component or a version: the file shows it where Jira holds a
  person's data, so sending it would overwrite the real text. `jira import-md` and `import-csv` refuse to update an issue from such a text or name too.
- A description is sent back from Markdown only when the way there and back changes nothing. What Markdown cannot show (a table, a panel, a colour, a macro, an image option) and what changes on the way
  back although nothing is named (a code parameter such as `{code:java|linenumbers=true}`, a literal star or underscore, `__init__`, a line that begins with `+` or `>`) is written as far as it goes; the file lists it in a `lossy:` line,
  and `push` refuses to send that description unless you add `--force`, because it would change more than you edited. A bullet made with `-` is no such change: it comes back as `*` (the words are the same, but Jira
  may draw the two bullets differently).
- A story points entry of `fields_json` may be a number or a text that holds one (`"8"`); anything else is an error for that file, never "no points" (a push would clear the field). A summary that begins
  and ends with a quote is written with one more pair, so that it comes back as it was.
- An exit code 3 is a decision for a person: read `jira diff`, then choose `jira push FILE --force` (the file wins), `jira pull DIR --update --force --issue KEY` (Jira wins), or edit the file
  to the text you want and push that.

**Compared with `import-md`:** `jira import-md FILE` with an `issue_key` writes **every** field of the file to Jira and does not look at what Jira holds now (it refuses a text that holds a placeholder);
`jira push` sends only what changed and says when Jira changed the same field. Use `import-md` to create issues and `push` to change them. `import-md` does not read the `links:` line: only `push` creates links from a file.

**A routine** for a repository that keeps its tickets as Markdown:

1. `jira diff DIR` before you start (read-only). Anything but "in step" is a decision.
2. `jira pull DIR --update --dry-run`, then without `--dry-run`: Jira's changes come in, yours stay.
3. Edit the files; commit them. `jira diff DIR` now lists them as `edited here`.
4. `jira push FILE --dry-run`, show the payload, `jira push FILE` for each file the person approves. Read the issue back with `jira issue KEY --json`.

An agent follows the same routine, and `diff` and `pull` into a scratch folder are safe at any time (see [use with AI agents](use-with-ai-agents.md)).

### Links between issues

The links of an issue are one line of its file, `links:`, in the front matter next to `labels:` and `fix_versions:`. It is a `key: value` line like the others, so `import-md` still reads the
file (and ignores the line):

```text
links: Blocks -> PROJ-2, Blocks <- PROJ-3, Has to be done after -> PROJ-7
```

Each entry is the **name** of the link type, an arrow and the key of the other issue. The arrow follows the relation: `Type -> KEY` means that this issue is the *outward* end ("this issue
blocks PROJ-2"), `Type <- KEY` that it is the *inward* end ("PROJ-3 blocks this issue", which Jira words "is blocked by"). `jira link-types` lists the names and the two phrases of each type. It is the
name and not the phrase, because a phrase is not unique ("requires" is the phrase of two types). To link a ticket, add an entry to the line (or add the line); to unlink, see below.

- Case and blanks do not matter (`blocks->proj-2` is `Blocks -> PROJ-2`), the order does not either, and an entry that is listed twice is one link.
- Jira stores a link with a direction, so `Relates -> PROJ-2` and `Relates <- PROJ-2` are two links for it, although both read "relates to".
- A type name with a comma, `<` or `>` cannot stand in the line: the links of an issue that has such a link type are not compared (`Not compared ... links of PROJ-7` says so, and its file gets no line). A line that cannot be
  read (`Blocks > PROJ-2`, an entry without a key, a link of the issue to itself) makes the file `unreadable` for `diff` and `pull`, and `push` refuses it, naming the entry: it never guesses what you meant, because a link is
  created in Jira.
- A file of an issue without links has no `links:` line (when all the links of an issue are removed in Jira, `pull --update` leaves the line in the file, empty). The other end of a link is another issue with another file: `PROJ-1 blocks PROJ-2` is `Blocks -> PROJ-2` in the file of PROJ-1 and `Blocks <- PROJ-1`
  in the file of PROJ-2, and when you link in one file the other file learns of it with the next `pull --update`.

The links are judged like every other field, against what the file and Jira last agreed on (`links=` in `jira_base`): a link that only the file lists is `edited here`, one that only Jira holds is
`jira changed`, the same list on both sides is in step, and the same list changed on both sides is a `conflict` (exit code 3, nothing sent). `jira pull` writes the `links:` line of a file that it makes; with `--update`
it writes the links of an issue that Jira changed, and it never overwrites a link that you wrote and did not push (not even with `--force`: only the fields that both sides changed are Jira's to win).

```text
$ jira diff docs/tickets --issue PROJ-1,PROJ-2
PROJ-1, PROJ-2: 2 files, 2 issues in Jira | in step 1 | edited here 1

KEY     STATE        DETAIL       FILE
PROJ-2  edited here  here: links  PROJ-2-fix-the-thing.md

$ jira push docs/tickets/PROJ-2-fix-the-thing.md --dry-run
would push PROJ-2 (links) from PROJ-2-fix-the-thing.md
would link  PROJ-2 is required by PROJ-4  (type 'Has to be done after')
{
  "type": {
    "name": "Has to be done after"
  },
  "outwardIssue": {
    "key": "PROJ-2"
  },
  "inwardIssue": {
    "key": "PROJ-4"
  }
}
Dry run: nothing was sent.

$ jira push docs/tickets/PROJ-2-fix-the-thing.md
pushed PROJ-2 (links) from PROJ-2-fix-the-thing.md
linked PROJ-2 is required by PROJ-4
```

**What `push` does with the links.** It creates the links that the file lists and Jira does not hold, one request each (`POST /issueLink`, the payload that the dry run shows; the issue that does what the type says is the
`outwardIssue`), after it has checked that the type exists (an unknown one is refused with the names that do, exit code 1) and that the other issue exists (else exit code 4), all before it sends anything. A link
that Jira holds already is never created a second time (it compares first). The fields of the issue go first, with the guard against a change since they were read, then the links; afterwards it reads the
issue back and notes the agreement in the file. A link needs the *Link Issues* permission: when Jira refuses it (exit code 5) the hint says so. `--json` adds `links[]` (the entry, the relation as Jira shows it
and the request body of each link that was or, with `--dry-run`, would be created) and `jiraOnlyLinks[]` to every outcome.

**The CLI deletes no link.** A link that you removed from the file but that Jira still holds is named, not removed, and it is no conflict: the other fields of the file go on being pushed.

```text
$ jira push docs/tickets/PROJ-2-fix-the-thing.md
unchanged PROJ-2 (PROJ-2-fix-the-thing.md): Has to be done after -> PROJ-4: jira has it; remove it in Jira (the CLI deletes no link)
```

Remove such a link in Jira's web interface (or put the entry back into the line). Until then the file stays `edited here` for its links, because the two do differ, and `push` says it every time; `pull --update --force` does not
bring the entry back, since it is an edit of yours (list it again by hand). If a link fails after another went through, the run ends with that error, the links that were created are noted, and the next `push` creates
only what is still missing.

**Files from version 1.0.0** have no record for the links and, if they were made before the links were kept in step, no `links:` line. Such a file is treated as if it had listed none on a day when Jira had none: the first `pull`
only records the agreement for a file whose issue has no links, and a file whose issue has links is `jira changed` (never `differs`, and never a reason to refuse a push of another field) until `jira pull --update` writes the line. A
link that you add to such a file is an edit that `push` sends without `--force` (when Jira holds links that the file does not list, both sides changed the list and that is a conflict like any other). Check it before you start: `jira diff DIR` after the upgrade lists the files whose issues have links.

## Compare a Markdown mirror with Jira with a script

A repository can also compare its tickets with a script of its own. The fetch step is one command:

```bash
jira export --project PROJ --out tmp/jira-sync/PROJ.json
```

When you write such a tool, convert in-process and skip the process per file:

```python
from jira_dc_cli.domain.wiki import markdown_to_wiki   # add <install folder> to sys.path first
wiki_text = markdown_to_wiki(markdown_text)
```

## Use the dump as data

```bash
python - <<'PY'
import json
dump = json.load(open("tmp/jira-sync/PROJ.json", encoding="utf-8"))
open_points = sum(i["storyPoints"] or 0 for i in dump["issues"].values() if i["status"] != "Done" and i["type"] != "Epic")
print(len(dump["issues"]), "issues,", open_points, "open points")
PY
```
