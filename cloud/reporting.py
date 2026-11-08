"""Relatório agregado AWS com as mesmas definições da execução local."""

from pipeline.dashboard import render_dashboard


def render_report(summary):
    return render_dashboard(summary)
