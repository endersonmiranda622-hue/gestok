"""Catálogo de permissões e padrões por função. A aplicação é feita no servidor (Etapa 3)."""
PERMS = ["dashboard.view", "items.view", "items.edit", "categories.manage", "warehouses.manage",
         "movements.create", "movements.view_all", "movements.view_own", "adjustments.request",
         "adjustments.approve", "reports.view", "audit.view", "users.manage", "settings.manage"]
DEFAULTS = {
    "admin": set(PERMS),
    "gestor": {"dashboard.view", "items.view", "movements.create", "movements.view_all",
               "adjustments.request", "reports.view"},
    "colaborador": {"items.view", "movements.create", "movements.view_own", "adjustments.request"},
}
