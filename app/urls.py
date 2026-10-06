from plain.assets.urls import AssetsRouter
from plain.auth.views import LogoutView
from plain.urls import Router, include, path

from . import (
    agents,
    api,
    browsers,
    check_types,
    checks,
    login_help,
    opencode,
    session_api,
    tasks,
    trees,
    views,
)
from .compare import CompareView
from .lineage import LineageView
from .network import NetworkEditor
from .sessions import SessionEditor, SessionStatus, SessionView


class AppRouter(Router):
    namespace = ""
    urls = (
        include("assets", AssetsRouter),
        path("login", views.LoginView, name="login"),
        path("logout", LogoutView, name="logout"),
        path("", views.Dashboard, name="index"),
        path("edit/network", NetworkEditor, name="network-editor"),
        path("edit/check", tasks.TaskEditor, name="task-editor"),
        path("fixes", tasks.FixesRedirect, name="fixes"),
        path("tasks/rules", tasks.RulesView, name="task-rules"),
        path("edit/rule", tasks.RuleEditor, name="rule-editor"),
        path("edit/check-type", check_types.CheckTypeEditor, name="check-type-editor"),
        path("edit/task-session", check_types.SessionEditor, name="task-session-editor"),
        path("edit/session", SessionEditor, name="session-editor"),
        path("sessions/<str:uid>", SessionView, name="session-request"),
        path("sessions/<str:uid>/status", SessionStatus, name="session-status"),
        path("edit/<str:kind>", views.Editor, name="editor"),
        path("personas/<int:id>/lineage", LineageView, name="lineage"),
        path("personas/<int:id>", views.PersonaView, name="persona"),
        path("runs/<int:id>", views.RunView, name="run"),
        path("runs/<int:id>/checks/<int:check_id>", views.RunView, name="check-result"),
        path("personas/<int:id>/browser", browsers.OpenBrowser, name="open-browser"),
        path("runs/<int:id>/browser", browsers.BrowserView, name="browser"),
        path("runs/<int:id>/browser-status", browsers.BrowserStatus, name="browser-status"),
        path("checks/<int:id>", checks.CheckHistoryView, name="check-history"),
        path("tree/<str:kind>/<int:id>", trees.RelatedRecords, name="related-records"),
        path("compare", CompareView, name="compare"),
        path("runs/<int:id>/tree", agents.SessionTreeView, name="session-tree"),
        path("runs/<int:id>/checks/<str:check_id>/activity", agents.CheckActivityView, name="check-activity"),
        path("runs/<int:id>/checks/<str:check_id>/conversation", agents.ConversationView, name="conversation"),
        path("agents", agents.AgentSessionsView, name="agents"),
        path("agents/opencode", opencode.OpenCodeProxy, name="opencode-root"),
        path("agents/opencode/<path:path>", opencode.OpenCodeProxy, name="opencode-proxy"),
        path("integrations", login_help.Integrations, name="integrations"),
        path("login-help", login_help.LoginHelp, name="login-help"),
        path("api/sessions", session_api.SessionsAPI, name="api-sessions"),
        path("api/sessions/<str:uid>", session_api.SessionsAPI, name="api-session"),
        path("api/<str:resource>", api.CollectionAPI, name="api-collection"),
        path("api/runs/<int:id>/<str:action>", api.RunAPI, name="api-run"),
        path("evidence/<int:id>/<str:filename>", views.EvidenceView, name="evidence"),
    )
