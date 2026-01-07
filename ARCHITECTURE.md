```
pleiades/
├── pyproject.toml              # ROOT: Defines the workspace & dev tools (Black, Isort)
├── README.md
│
├── atlas/                      # LIBRARY: Shared Infra
│   ├── pyproject.toml          # Deps: Pydantic, Psycopg, GCS-Client
│   ├── src/                    # Src layout avoids import hacks
│   │   └── atlas/
│   │       ├── __init__.py
│   │       ├── db.py
│   │       ├── config.py
│   │       └── ...
│
├── maia/                       # SERVICE: Collection
│   ├── pyproject.toml          # Deps: Atlas (Local), Google-API-Python-Client
│   ├── Dockerfile              # Slim image, only installs Maia + Atlas
│   ├── src/
│   │   └── maia/
│   │       ├── __init__.py
│   │       ├── hunter.py
│   │       └── ...
│
└── alkyone/                    # SERVICE: Testing & QA
    ├── pyproject.toml          # Deps: Pytest, Httpx, VCRpy
    ├── Dockerfile              # Fat image, installs everything for integration tests
    ├── src/
    │   └── alkyone/            # Test logic, fixtures, chaos monkeys
    │       ├── conftest.py
    │       └── ...
    └── tests/
        ├── unit/
        └── integration/
```