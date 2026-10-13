"""v1 API router — aggregates all endpoint routers.

New endpoint modules (subscriptions, events, deliveries, tenants) get
included here as we build them.

Health probes are deliberately *not* here: they are mounted at the root in
`main.py` so load balancers and kubelets don't have to know about API
versioning, and so `/healthz` never moves when the API version does.
"""

from fastapi import APIRouter

api_router = APIRouter()
