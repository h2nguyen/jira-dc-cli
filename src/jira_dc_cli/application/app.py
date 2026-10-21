"""The application as the driving adapters see it: one object holding every use case.

`Application.create` wires the use cases from ports only, so the same wiring serves the real
adapters (see `bootstrap.py`) and the fakes of the tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .comments import AddComment, ListComments
from .doctor import Doctor
from .extras import ConvertMarkdown, InstallSkill, RawApi, ReadDocs
from .imports import ImportCsv, ImportMarkdown, ImportMarkdownDirectory
from .issues import BuildReport, ExportBacklog, ListFields, SearchIssues, SuggestBranch, ViewIssue
from .linking import LinkIssues, ListLinkTypes
from .ports import Clock, ConfigStore, Diagnostics, Docs, Files, JiraGateway
from .projects import ListAdminProjects, ListProjects, ShowCreateMetadata, ShowProject
from .search import FindIssues
from .session import ConfigService, Ping
from .settings import Settings
from .sync import DiffMirror, PullMirror, PushMirror, SyncEngine
from .workflow import TransitionIssue
from .writing import CreateIssue, CreateIssueFromPayload, UpdateIssue, UpdateIssueFromPayload


@dataclass
class Application:
    settings: Settings
    diagnostics: Diagnostics
    docs: Docs
    files: Files
    view_issue: ViewIssue
    search_issues: SearchIssues
    find_issues: FindIssues
    suggest_branch: SuggestBranch
    build_report: BuildReport
    export_backlog: ExportBacklog
    list_fields: ListFields
    transition_issue: TransitionIssue
    add_comment: AddComment
    list_comments: ListComments
    link_issues: LinkIssues
    list_link_types: ListLinkTypes
    update_issue: UpdateIssue
    update_issue_from_payload: UpdateIssueFromPayload
    create_issue: CreateIssue
    create_issue_from_payload: CreateIssueFromPayload
    list_projects: ListProjects
    list_admin_projects: ListAdminProjects
    show_project: ShowProject
    show_create_metadata: ShowCreateMetadata
    import_markdown: ImportMarkdown
    import_markdown_directory: ImportMarkdownDirectory
    import_csv: ImportCsv
    diff_mirror: DiffMirror
    pull_mirror: PullMirror
    push_mirror: PushMirror
    config: ConfigService
    ping: Ping
    doctor: Doctor
    read_docs: ReadDocs
    install_skill: InstallSkill
    convert_markdown: ConvertMarkdown
    raw_api: RawApi
    closers: tuple[Callable[[], None], ...] = ()

    def close(self) -> None:
        """Release what the adapters hold open, such as the connection to Jira."""
        for closer in self.closers:
            closer()

    @classmethod
    def create(
        cls,
        *,
        settings: Settings,
        gateway: JiraGateway,
        store: ConfigStore,
        files: Files,
        clock: Clock,
        docs: Docs,
        diagnostics: Diagnostics,
        environ: Mapping[str, str],
        runtime: Mapping[str, str],
        closers: tuple[Callable[[], None], ...] = (),
    ) -> "Application":
        create_issue = CreateIssue(gateway, settings)
        update_issue = UpdateIssue(gateway)
        update_from_payload = UpdateIssueFromPayload(gateway)
        engine = SyncEngine(gateway, settings, files, clock)
        import_markdown = ImportMarkdown(files, gateway, settings, create_issue, update_issue, diagnostics)
        return cls(
            settings=settings,
            diagnostics=diagnostics,
            docs=docs,
            files=files,
            view_issue=ViewIssue(gateway, settings),
            search_issues=SearchIssues(gateway, settings),
            find_issues=FindIssues(gateway, settings),
            suggest_branch=SuggestBranch(gateway),
            build_report=BuildReport(gateway, settings, diagnostics),
            export_backlog=ExportBacklog(gateway, settings, clock),
            list_fields=ListFields(gateway),
            transition_issue=TransitionIssue(gateway),
            add_comment=AddComment(gateway),
            list_comments=ListComments(gateway, settings),
            link_issues=LinkIssues(gateway),
            list_link_types=ListLinkTypes(gateway),
            update_issue=update_issue,
            update_issue_from_payload=update_from_payload,
            create_issue=create_issue,
            create_issue_from_payload=CreateIssueFromPayload(gateway),
            list_projects=ListProjects(gateway, settings),
            list_admin_projects=ListAdminProjects(gateway, settings, diagnostics),
            show_project=ShowProject(gateway, settings),
            show_create_metadata=ShowCreateMetadata(gateway),
            import_markdown=import_markdown,
            import_markdown_directory=ImportMarkdownDirectory(files, gateway, settings, import_markdown, diagnostics),
            import_csv=ImportCsv(files, settings, create_issue, update_issue),
            diff_mirror=DiffMirror(engine),
            pull_mirror=PullMirror(engine, files),
            push_mirror=PushMirror(engine, gateway, files, update_from_payload),
            config=ConfigService(store, settings, environ, gateway),
            ping=Ping(gateway, settings),
            doctor=Doctor(gateway, settings, files, store.path, runtime),
            read_docs=ReadDocs(docs),
            install_skill=InstallSkill(docs, files),
            convert_markdown=ConvertMarkdown(),
            raw_api=RawApi(gateway, settings),
            closers=closers,
        )
