"""Ponto de entrada de produção: gunicorn -c deploy/gunicorn.conf.py wsgi:app"""
from app import create_app
app = create_app()   # falha ao iniciar se a configuração de produção for insegura (ex.: GESTOK_SECRET_KEY ausente/curta)
