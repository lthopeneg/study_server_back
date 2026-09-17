"""Minimal deployment readiness endpoint."""

import os

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "revision": os.getenv("APP_REVISION", "unknown"),
    })
