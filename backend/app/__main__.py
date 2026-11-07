# -*- coding: utf-8 -*-
"""Arranque sin Docker:  python -m app   (desde la carpeta backend/)"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app",
                host=os.environ.get("CLONAR_HOST", "127.0.0.1"),
                port=int(os.environ.get("PUERTO", "8080")),
                log_level="warning")
