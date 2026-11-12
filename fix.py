import os, glob
for f in glob.glob("app/*.py"):
    with open(f, 'r', encoding='utf-8', errors='ignore') as file:
        content = file.read()
    original = content
    # Troca todos os jeitos errados de importar get_db
    content = content.replace("from . import get_db", "from .db import db\ndef get_db(): return db")
    content = content.replace("from app import get_db", "from app.db import db\ndef get_db(): return db")
    content = content.replace("from app.db import get_db", "from app.db import db\ndef get_db(): return db")
    content = content.replace("from .db import get_db", "from .db import db\ndef get_db(): return db")
    content = content.replace("from app import db, get_db", "from app.db import db")
    content = content.replace("from . import db, get_db", "from .db import db")
    if content != original:
        print(f"Consertado: {f}")
        with open(f, 'w', encoding='utf-8') as file:
            file.write(content)
print("Fim!")