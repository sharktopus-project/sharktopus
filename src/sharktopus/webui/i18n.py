"""Lightweight i18n for the WebUI.

English is the source-of-truth: keys are the English strings themselves,
so a missing translation falls back transparently to English. Switching
languages is per-browser via a ``lang`` cookie and a tiny redirect
endpoint at ``/lang/{code}``.

Adding a language: append to :data:`LANGS` and provide a translation
dict here; that's it. New strings are translated incrementally — any
unwrapped string just stays in English when ``lang=pt``.
"""

from __future__ import annotations

from fastapi import Request

LANGS: dict[str, str] = {
    "en": "English",
    "pt": "Português",
}
DEFAULT = "en"


_PT: dict[str, str] = {
    # ------- header / nav -----------------------------------------------
    "cloud-native GRIB cropper": "cropper de GRIB nativo da nuvem",
    "GFS today \u00b7 HRRR tomorrow \u00b7 who knows next?":
        "GFS hoje \u00b7 HRRR amanh\u00e3 \u00b7 quem sabe o que vem?",
    "Dashboard": "Painel",
    "Submit": "Enviar",
    "Jobs": "Trabalhos",
    "Inventory": "Invent\u00e1rio",
    "Quota": "Quota",
    "Sources": "Fontes",
    "Setup": "Setup",
    "Credentials": "Credenciais",
    "Settings": "Ajustes",
    "Help": "Ajuda",
    "About": "Sobre",
    # ------- footer ------------------------------------------------------
    "independent project": "projeto independente",
    "MIT-licensed": "licen\u00e7a MIT",
    "not a product of any listed institution.":
        "n\u00e3o \u00e9 produto de nenhuma institui\u00e7\u00e3o listada.",
    "About & supporters": "Sobre & apoiadores",
    # ------- dashboard ---------------------------------------------------
    "Welcome to sharktopus": "Bem-vindo ao sharktopus",
    "Crop NOAA GFS near the source, download only what matters, "
    "and keep cloud spend in check. Anything the CLI does, you can do here.":
        "Recorte os GFS do NOAA perto da origem, baixe s\u00f3 o que "
        "importa e mantenha o or\u00e7amento de nuvem sob controle. "
        "Tudo o que a CLI faz, voc\u00ea faz aqui tamb\u00e9m.",
    "New download": "Novo download",
    "Browse inventory": "Ver invent\u00e1rio",
    "Cloud quota": "Quota da nuvem",
    "total": "total",
    "running": "rodando",
    "queued": "na fila",
    "ok": "ok",
    "failed": "falhou",
    "See all jobs \u2192": "Ver todos os trabalhos \u2192",
    "files": "arquivos",
    "on disk": "em disco",
    "Browse files \u2192": "Navegar nos arquivos \u2192",
    "Clouds": "Nuvens",
    "aws \u00b7 gcloud \u00b7 azure \u2014 track free-tier usage and "
    "provision new projects.":
        "aws \u00b7 gcloud \u00b7 azure \u2014 acompanhe o uso do "
        "tier gratuito e provisione projetos.",
    "Quota dashboard \u2192": "Painel de quota \u2192",
    "Recent jobs": "Trabalhos recentes",
    "All jobs \u2192": "Todos \u2192",
    "Name": "Nome",
    "Status": "Status",
    "Progress": "Progresso",
    "Duration": "Dura\u00e7\u00e3o",
    "Created": "Criado",
    "No jobs yet.": "Nenhum trabalho ainda.",
    "Start one \u2192": "Iniciar um \u2192",
    # ------- language switcher -------------------------------------------
    "Language": "Idioma",
}

TRANSLATIONS: dict[str, dict[str, str]] = {"pt": _PT, "en": {}}


def current_lang(request: Request) -> str:
    """Return the active language code for *request* (always in :data:`LANGS`)."""
    code = request.cookies.get("lang", DEFAULT)
    return code if code in LANGS else DEFAULT


def make_t(lang: str):
    """Return a ``t(text)`` translator bound to *lang*."""
    table = TRANSLATIONS.get(lang) or {}

    def t(text: str) -> str:
        return table.get(text, text)

    return t
