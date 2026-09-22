"""Flask routes."""

from __future__ import annotations

import tempfile
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from config import EXPORT
from exporter import export_to_csv

from dashboard.services import DashboardService

bp = Blueprint("dashboard", __name__)
service = DashboardService()


def _ensure_db() -> bool:
    if not service.ready:
        service.initialize()
    return service.ready


@bp.before_request
def _init_service() -> None:
    if not service.ready:
        service.initialize()


@bp.route("/", methods=["GET", "POST"])
def home():
    if not _ensure_db():
        return render_template(
            "error.html",
            title="Unavailable",
            message=service.init_error or "Could not open the database.",
        )

    summary = service.get_home_summary()
    form = {
        "url": request.form.get("url", "").strip(),
        "company": request.form.get("company", "").strip(),
        "keyword": request.form.get("keyword", "").strip(),
    }

    if request.method == "POST":
        if not form["url"]:
            flash("Enter a careers page URL.", "error")
            return render_template("dashboard.html", summary=summary, form=form)

        result = service.collect(
            form["url"],
            company_name=form["company"],
            keyword=form["keyword"],
        )
        if result["ok"]:
            flash(result["message"], "success")
            return redirect(url_for("dashboard.jobs", run=result.get("run_id")))

        flash(result["message"], "error")
        return render_template("dashboard.html", summary=summary, form=form)

    return render_template(
        "dashboard.html",
        summary=summary if not summary.get("error") else {},
        form=form,
    )


@bp.route("/jobs")
def jobs():
    if not _ensure_db():
        return render_template(
            "error.html",
            title="Jobs unavailable",
            message=service.init_error,
        )

    page = request.args.get("page", 1, type=int)
    q = request.args.get("q", "")
    company = request.args.get("company", "")
    run_id = request.args.get("run", type=int)

    data = service.get_jobs_page(page=page, q=q, company=company, run_id=run_id)
    if data.get("error"):
        return render_template("error.html", title="Jobs error", message=data["error"])

    return render_template(
        "jobs.html",
        jobs=data["jobs"],
        total=data["total"],
        page=data["page"],
        total_pages=data["total_pages"],
        q=q,
        company=company,
        run_id=run_id,
        companies=service.list_company_options(),
    )


@bp.route("/history")
def history():
    if not _ensure_db():
        return render_template("error.html", title="History", message=service.init_error)

    data = service.get_runs()
    if data.get("error"):
        return render_template("error.html", title="History", message=data["error"])
    return render_template("runs.html", runs=data["runs"], total=data["total"])


@bp.route("/export", methods=["GET", "POST"])
def export():
    if not _ensure_db():
        flash(service.init_error or "Database unavailable.", "error")
        return redirect(url_for("dashboard.home"))

    q = request.values.get("q", "")
    company = request.values.get("company", "")
    run_id = request.values.get("run", type=int)

    jobs, err = service.get_export_jobs(q=q, company=company, run_id=run_id)
    if err:
        flash(err, "warning")
        return redirect(url_for("dashboard.jobs", q=q, company=company, run=run_id))

    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".csv",
        delete=False,
        encoding=EXPORT["encoding"],
    )
    tmp_path = Path(tmp.name)
    tmp.close()

    if not export_to_csv(jobs, output_path=str(tmp_path)):
        flash("Export failed. Check logs or file permissions.", "error")
        return redirect(url_for("dashboard.jobs"))

    return send_file(
        tmp_path,
        as_attachment=True,
        download_name="jobs.csv",
        mimetype="text/csv",
    )
