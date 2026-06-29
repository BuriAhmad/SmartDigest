#!/bin/sh
set -eu

python -m alembic upgrade head
python -m app.cli seed_sources
