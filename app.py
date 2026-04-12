from __future__ import annotations

import csv
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
application = app
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "tektutors-dev-secret-change-this")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONTACT_FILE = DATA_DIR / "contact_submissions.csv"
NEWSLETTER_FILE = DATA_DIR / "newsletter_subscriptions.csv"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PROGRAMS = [
    {
        "name": "Data Analysis",
        "summary": "Turn raw data into clear decisions using practical analytics frameworks.",
        "duration": "8-10 weeks",
        "level": "Beginner to Intermediate",
    },
    {
        "name": "Data Visualization",
        "summary": "Translate complex findings into clear visuals, dashboards, and decision-ready stories.",
        "duration": "6-8 weeks",
        "level": "Beginner to Intermediate",
    },
    {
        "name": "Statistical Analysis",
        "summary": "Use probability, hypothesis testing, and inference to uncover patterns with confidence.",
        "duration": "8-10 weeks",
        "level": "Intermediate",
    },
    {
        "name": "Data Governance and Quality",
        "summary": "Build trusted data foundations with governance practices, validation checks, and quality controls.",
        "duration": "8-10 weeks",
        "level": "Intermediate",
    },
    {
        "name": "Applied Python for Data",
        "summary": "Use Python for cleaning, analysis, automation, and practical data workflows from day one.",
        "duration": "8-12 weeks",
        "level": "Beginner to Intermediate",
    },
    {
        "name": "Business Intelligence",
        "summary": "Design dashboards that leaders trust and use daily.",
        "duration": "8-12 weeks",
        "level": "Intermediate",
    },
    {
        "name": "Predictive Analytics",
        "summary": "Build forecasting and predictive models that support smarter planning and business action.",
        "duration": "10-14 weeks",
        "level": "Intermediate to Advanced",
    },
    {
        "name": "Data Science",
        "summary": "Build data products with statistics, experimentation, and production-ready thinking.",
        "duration": "12-16 weeks",
        "level": "Intermediate to Advanced",
    },
    {
        "name": "Machine Learning",
        "summary": "Master model development, evaluation, and deployment with mentor guidance.",
        "duration": "12-18 weeks",
        "level": "Advanced",
    },
    {
        "name": "Artificial Intelligence",
        "summary": "Apply modern AI workflows to real business and product challenges.",
        "duration": "10-16 weeks",
        "level": "Intermediate to Advanced",
    },
]

TOOLS = ["SQL", "Excel", "Power BI", "R", "Python"]

TESTIMONIALS = [
    {
        "name": "Adaeze O.",
        "role": "Business Analyst, FinTech",
        "quote": "I went from overwhelmed to confident in 10 weeks. The 1-on-1 guidance changed everything.",
    },
    {
        "name": "Michael E.",
        "role": "Data Analyst, Retail",
        "quote": "Night sessions made it possible to learn while keeping my full-time job.",
    },
    {
        "name": "Chidinma A.",
        "role": "BI Specialist, Logistics",
        "quote": "Every class solved real business problems. I shipped my first dashboard before graduation.",
    },
]

HOW_IT_WORKS = [
    {
        "title": "1. Book Your Free Strategy Session",
        "description": "We assess your current level, career direction, and target role.",
    },
    {
        "title": "2. Get a Personalized Learning Roadmap",
        "description": "You receive a focused plan built around your pace and schedule.",
    },
    {
        "title": "3. Train 1-on-1 With Real Mentors",
        "description": "Hands-on sessions, feedback loops, and guided projects every week.",
    },
    {
        "title": "4. Build Portfolio + Career Readiness",
        "description": "You leave with practical experience, confidence, and interview-ready proof.",
    },
]


def initialize_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not CONTACT_FILE.exists():
        with CONTACT_FILE.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "timestamp_utc",
                    "name",
                    "email",
                    "phone",
                    "program",
                    "availability",
                    "message",
                    "ip_address",
                    "user_agent",
                ]
            )

    if not NEWSLETTER_FILE.exists():
        with NEWSLETTER_FILE.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["timestamp_utc", "email", "interest", "ip_address"])


def append_csv_row(file_path: Path, row: list[str]) -> None:
    with file_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(row)


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email.strip()))


@app.context_processor
def inject_site_context() -> dict:
    return {"current_year": datetime.now(tz=UTC).year}


@app.route("/")
def index():
    return render_template(
        "index.html",
        page_title="Tektutors | Learn Data Skills Your Way",
        meta_description="Premium 1-on-1 data analytics mentorship in Nigeria with flexible schedules, including night classes.",
        active_page="home",
        programs=PROGRAMS,
        tools=TOOLS,
        testimonials=TESTIMONIALS,
        process_steps=HOW_IT_WORKS,
    )


@app.route("/about")
def about():
    return render_template(
        "about.html",
        page_title="About Tektutors | Premium Data Mentorship",
        meta_description="Learn how Tektutors helps professionals become confident data experts through personalized mentorship.",
        active_page="about",
    )


@app.route("/programs")
def programs():
    return render_template(
        "programs.html",
        page_title="Programs | Data Analytics Training at Tektutors",
        meta_description="Explore hands-on training in analytics, visualization, Python, governance, predictive modeling, and AI with 1-on-1 coaching.",
        active_page="programs",
        programs=PROGRAMS,
        tools=TOOLS,
    )


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        honeypot = request.form.get("company", "").strip()
        if honeypot:
            return redirect(url_for("contact"))

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        program = request.form.get("program", "").strip()
        availability = request.form.get("availability", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not message:
            flash("Please complete your name, email, and message so we can help you quickly.", "error")
            return redirect(url_for("contact") + "#contact-form")

        if not is_valid_email(email):
            flash("Please provide a valid email address.", "error")
            return redirect(url_for("contact") + "#contact-form")

        try:
            append_csv_row(
                CONTACT_FILE,
                [
                    datetime.now(tz=UTC).isoformat(),
                    name,
                    email,
                    phone,
                    program,
                    availability,
                    message,
                    request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    request.headers.get("User-Agent", "")[:200],
                ],
            )
        except OSError:
            flash("We could not submit your request right now. Please try again shortly.", "error")
            return redirect(url_for("contact") + "#contact-form")

        flash("Thanks for reaching out. A mentor will contact you within 24 hours.", "success")
        return redirect(url_for("contact") + "#contact-form")

    return render_template(
        "contact.html",
        page_title="Contact Tektutors | Book a Free Consultation",
        meta_description="Contact Tektutors to start personalized data analytics mentorship with flexible learning schedules.",
        active_page="contact",
        programs=PROGRAMS,
    )


@app.route("/newsletter", methods=["POST"])
def newsletter():
    email = request.form.get("email", "").strip().lower()
    interest = request.form.get("interest", "").strip()
    destination = request.form.get("next_page", "index").strip()
    allowed_destinations = {"index", "about", "programs", "contact"}

    if destination not in allowed_destinations:
        destination = "index"

    if not email or not is_valid_email(email):
        flash("Please enter a valid email to subscribe.", "error")
        return redirect(url_for(destination) + "#newsletter")

    try:
        append_csv_row(
            NEWSLETTER_FILE,
            [
                datetime.now(tz=UTC).isoformat(),
                email,
                interest,
                request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            ],
        )
    except OSError:
        flash("Subscription failed. Please try again shortly.", "error")
        return redirect(url_for(destination) + "#newsletter")

    flash("You are in. Expect practical insights and program updates in your inbox.", "success")
    return redirect(url_for(destination) + "#newsletter")


initialize_storage()


if __name__ == "__main__":
    app.run(debug=True)
