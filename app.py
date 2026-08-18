from __future__ import annotations

import csv
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values, load_dotenv
from flask import Flask, flash, g, jsonify, make_response, redirect, render_template, request, send_from_directory, session, url_for
from flask_mail import Mail, Message
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import zoho_meeting

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # PostgreSQL is optional for local SQLite development.
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
PROJECT_ENV = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
load_dotenv(ENV_FILE, override=False)

app = Flask(__name__)
application = app
# Enable automatic template reloading (works in dev and prod)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
APP_ENV = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).strip().lower() or "development"
IS_PRODUCTION = APP_ENV == "production"
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "true" if IS_PRODUCTION else "false").lower() == "true"
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "tektutors.com.ng").strip().lower() or "tektutors.com.ng"
secret_key = os.environ.get("SECRET_KEY")
if IS_PRODUCTION and not secret_key:
    raise RuntimeError("SECRET_KEY must be set when APP_ENV=production.")
app.config["SECRET_KEY"] = secret_key or "tektutors-dev-secret-change-this"
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", f"mail.{MAIL_DOMAIN}")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 465))
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "False").lower() == "true"
app.config["MAIL_USE_SSL"] = os.environ.get("MAIL_USE_SSL", "True").lower() == "true"
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = (
    os.environ.get("MAIL_DEFAULT_SENDER")
    or os.environ.get("MAIL_USERNAME")
    or os.environ.get("ADMIN_EMAIL")
    or f"no-reply@{MAIL_DOMAIN}"
)
app.config["MAIL_TIMEOUT"] = int(os.environ.get("MAIL_TIMEOUT", 30))
app.config["GA_TRACKING_ID"] = os.environ.get("GA_TRACKING_ID")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", str(5 * 1024 * 1024)))
app.config["MAINTENANCE_MODE"] = os.environ.get("MAINTENANCE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
app.config["MAINTENANCE_RETRY_AFTER"] = int(os.environ.get("MAINTENANCE_RETRY_AFTER", "3600"))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["PREFERRED_URL_SCHEME"] = "https" if IS_PRODUCTION else "http"

if TRUST_PROXY_HEADERS:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[assignment]

mail = Mail(app)

DATA_DIR = BASE_DIR / "data"
CONTACT_FILE = DATA_DIR / "contact_submissions.csv"
NEWSLETTER_FILE = DATA_DIR / "newsletter_subscriptions.csv"
REGISTRATION_FILE = DATA_DIR / "registration_submissions.csv"
CAREERS_FILE = DATA_DIR / "careers_applications.csv"
DATABASE_FILE = DATA_DIR / "tektutors_lms.db"
DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("POSTGRESQL_URL")
    or PROJECT_ENV.get("DATABASE_URL")
    or PROJECT_ENV.get("POSTGRES_URL")
    or PROJECT_ENV.get("POSTGRESQL_URL")
)
DATABASE_ENGINE = "postgresql" if DATABASE_URL else "sqlite"
DATABASE_CONNECT_TIMEOUT_SECONDS = int(os.environ.get("DATABASE_CONNECT_TIMEOUT_SECONDS") or PROJECT_ENV.get("DATABASE_CONNECT_TIMEOUT_SECONDS") or 10)
UPLOADS_DIR = DATA_DIR / "uploads"
MATERIALS_DIR = UPLOADS_DIR / "materials"
SUBMISSIONS_DIR = UPLOADS_DIR / "submissions"
REGISTRATION_RECEIPTS_DIR = UPLOADS_DIR / "registrations"
CAREERS_CVS_DIR = UPLOADS_DIR / "careers"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_PATTERN = re.compile(r"https?://[^\s<]+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ".,!?;:)]}"
DEFAULT_DEMO_PASSWORD = "DemoPass123!"
DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", f"admin@{MAIL_DOMAIN}")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*"
FORMS_NOTIFICATION_EMAIL = os.environ.get("FORMS_NOTIFICATION_EMAIL", DEFAULT_ADMIN_EMAIL)
REQUIRE_FORM_EMAIL_DELIVERY = os.environ.get("REQUIRE_FORM_EMAIL_DELIVERY", "true" if IS_PRODUCTION else "false").lower() == "true"
ALLOW_SQLITE_IN_PRODUCTION = os.environ.get("ALLOW_SQLITE_IN_PRODUCTION", "false").lower() == "true"
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
}
ALLOWED_CV_EXTENSIONS = {".pdf", ".doc", ".docx"}
PUBLIC_FORM_RATE_LIMITS: dict[str, list[float]] = defaultdict(list)
PUBLIC_FORM_BLOCK_WINDOW_SECONDS = 15 * 60
PUBLIC_FORM_MIN_SECONDS = 3
PUBLIC_FORM_MAX_LINKS = 3
FEEDBACK_MILESTONES = (4, 7)
FEEDBACK_RATING_FIELDS = [
    ("course_experience", "Course experience"),
    ("course_content", "Course content"),
    ("mentor_methodology", "Mentor teaching methodology"),
    ("mentor_experience", "Mentor experience"),
    ("mentor_knowledge", "Mentor knowledge"),
    ("lms_ease", "Learning Hub ease of use"),
]
FEEDBACK_MAX_SCORE = len(FEEDBACK_RATING_FIELDS) * 5
PROJECT_UNLOCK_PROGRESS = 85
MEETING_REMINDER_LEAD_TIME = timedelta(minutes=30)
CHALLENGE_DUE_REMINDER_LEAD_TIME = timedelta(hours=24)
MEETING_REMINDER_CHECK_INTERVAL_SECONDS = int(os.environ.get("MEETING_REMINDER_CHECK_INTERVAL_SECONDS", 60))
ENABLE_BACKGROUND_SCHEDULER = (
    os.environ.get("ENABLE_BACKGROUND_SCHEDULER")
    or PROJECT_ENV.get("ENABLE_BACKGROUND_SCHEDULER")
    or ("true" if not IS_PRODUCTION else "false")
).lower() == "true"
_meeting_reminder_scheduler_started = False
DB_INTEGRITY_ERRORS: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,)
if psycopg is not None:
    DB_INTEGRITY_ERRORS = DB_INTEGRITY_ERRORS + (psycopg.IntegrityError,)

PROGRAMS = [
    {
        "name": "Data Analysis",
        "summary": "Turn raw data into clear decisions using practical analytics frameworks.",
        "duration": "8-10 weeks",
        "level": "Beginner to Intermediate",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></svg>',
    },
    {
        "name": "Data Visualization",
        "summary": "Translate complex findings into clear visuals, dashboards, and decision-ready stories.",
        "duration": "6-8 weeks",
        "level": "Beginner to Intermediate",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/></svg>',
    },
    {
        "name": "Statistical Analysis",
        "summary": "Use probability, hypothesis testing, and inference to uncover patterns with confidence.",
        "duration": "8-10 weeks",
        "level": "Intermediate",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>',
    },
    {
        "name": "Data Governance and Quality",
        "summary": "Build trusted data foundations with governance practices, validation checks, and quality controls.",
        "duration": "8-10 weeks",
        "level": "Intermediate",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/></svg>',
    },
    {
        "name": "Applied Python for Data",
        "summary": "Use Python for cleaning, analysis, automation, and practical data workflows from day one.",
        "duration": "8-12 weeks",
        "level": "Beginner to Intermediate",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
    },
    {
        "name": "Business Intelligence",
        "summary": "Design dashboards that leaders trust and use daily.",
        "duration": "8-12 weeks",
        "level": "Intermediate",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="14" x="2" y="7" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>',
    },
    {
        "name": "Predictive Analytics",
        "summary": "Build forecasting and predictive models that support smarter planning and business action.",
        "duration": "10-14 weeks",
        "level": "Intermediate to Advanced",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
    },
    {
        "name": "Data Science",
        "summary": "Build data products with statistics, experimentation, and production-ready thinking.",
        "duration": "12-16 weeks",
        "level": "Intermediate to Advanced",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2v7.31"/><path d="M14 9.3V1.99"/><path d="M8.5 2h7"/><path d="M14 9.3a6.5 6.5 0 1 1-4 0"/><path d="M5.52 16h12.96"/></svg>',
    },
    {
        "name": "Machine Learning",
        "summary": "Master model development, evaluation, and deployment with mentor guidance.",
        "duration": "12-18 weeks",
        "level": "Advanced",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2" /><path d="M9 9h6v6H9z" /><line x1="9" y1="1" x2="9" y2="4" /><line x1="15" y1="1" x2="15" y2="4" /><line x1="9" y1="20" x2="9" y2="23" /><line x1="15" y1="20" x2="15" y2="23" /><line x1="20" y1="9" x2="23" y2="9" /><line x1="20" y1="14" x2="23" y2="14" /><line x1="1" y1="9" x2="4" y2="9" /><line x1="1" y1="14" x2="4" y2="14" /></svg>',
    },
    {
        "name": "Artificial Intelligence",
        "summary": "Apply modern AI workflows to real business and product challenges.",
        "duration": "10-16 weeks",
        "level": "Intermediate to Advanced",
        "icon": '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" /></svg>',
    },
]

TOOLS = ["SQL", "Excel", "Power BI", "R", "Python"]


@app.template_filter("linkify")
def linkify(value: object, label: str = "Open shared link") -> Markup:
    text = str(escape(value or ""))
    link_label = str(escape(label or "Open shared link"))

    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        trailing = ""
        while url and url[-1] in TRAILING_URL_PUNCTUATION:
            trailing = url[-1] + trailing
            url = url[:-1]
        return f'<a class="lms-text-link" href="{url}" target="_blank" rel="noopener noreferrer">{link_label}</a>{trailing}'

    return Markup(URL_PATTERN.sub(replace_url, text).replace("\n", "<br>"))

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
    {
        "name": "Daniel T.",
        "role": "Data Scientist, Telecom",
        "quote": "The curriculum was intense but exactly what I needed. I secured a promotion two months after finishing.",
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

LMS_FEATURES = [
    {
        "title": "Class stream",
        "description": "Mentors post announcements, live session reminders, and feedback in one shared learning stream.",
    },
    {
        "title": "Assignments and deadlines",
        "description": "Track quizzes, capstone tasks, and portfolio submissions with clear due dates and status labels.",
    },
    {
        "title": "Weekly modules",
        "description": "Learners move through structured topics, attached resources, and guided outcomes week by week.",
    },
    {
        "title": "Progress visibility",
        "description": "Learners and mentors can see completion rates, attendance, and upcoming priorities at a glance.",
    },
]

ACADEMIC_QUALIFICATIONS = ["Degree", "HND", "OND", "O Level", "Others"]
COURSE_OPTIONS = [
    "Business Intelligence",
    "Data Visualization with Power BI",
    "Python for Data Analysis",
    "SQL for Data Analytics",
    "R Programming for Data Analysis",
    "Advanced Statistical Analysis",
    "Data Analysis with Power BI & SQL",
    "Machine Learning",
    "Artificial Intelligence",
    "Data Analysis with Excel & Google Spreadsheet",
]
TRAINING_DAYS = ["Sunday", "Saturday", "Friday", "Thursday", "Wednesday", "Tuesday", "Monday"]

LMS_CLASSROOMS = [
    {
        "slug": "data-analysis-bootcamp",
        "title": "Data Analysis Bootcamp",
        "code": "TT-DA-204",
        "mentor": "Ada Nwosu",
        "cohort": "May 2026 Evening Cohort",
        "schedule": "Mon, Wed, Fri at 7:30 PM WAT",
        "course_duration": "4 weeks",
        "course_start_date": "2026-05-01",
        "hero_metric": "84% average progress",
        "description": "A practical classroom for analysts learning Excel, SQL, and business storytelling through weekly cases.",
        "theme": "analytics",
        "completion": 84,
        "live_sessions": 2,
        "announcements": [
            {
                "kind": "announcement",
                "title": "Week 5 dashboard feedback is live",
                "body": "Your dashboard comments are now available. Review the notes before the Saturday mentor lab so we can spend time on improvements, not setup.",
                "posted_at": "2026-05-01T18:00:00+01:00",
            },
            {
                "kind": "resource",
                "title": "New resource: Stakeholder-ready chart checklist",
                "body": "Use this checklist before submitting dashboards. It covers title clarity, color discipline, annotation, and business context.",
                "posted_at": "2026-04-30T15:30:00+01:00",
            },
        ],
        "modules": [
            {
                "title": "Module 5: Dashboard Thinking",
                "status": "In Progress",
                "summary": "Transform cleaned data into decision-ready reports for executives and operations teams.",
                "items": ["Lesson video: KPI framing", "Practice file: Retail performance", "Mentor checklist: chart critiques"],
            },
            {
                "title": "Module 6: SQL for Reporting",
                "status": "Next Up",
                "summary": "Write reusable queries that answer business questions quickly and cleanly.",
                "items": ["Reading: joins explained simply", "Lab: customer cohorts", "Quiz: SQL patterns"],
            },
        ],
        "assignments": [
            {
                "title": "Retail sales cleanup task",
                "description": "Clean the raw sales workbook, document three quality issues, and upload your final Excel file plus one-slide insight summary.",
                "due_at": "2026-05-04T20:00:00+01:00",
            },
            {
                "title": "Dashboard storyboard draft",
                "description": "Create a draft dashboard layout and explain which audience each KPI supports.",
                "due_at": "2026-05-07T20:00:00+01:00",
            },
            {
                "title": "SQL reporting quiz",
                "description": "Answer ten scenario-based SQL questions for joins, grouping, and filtering.",
                "due_at": "2026-05-10T18:00:00+01:00",
            },
        ],
    },
    {
        "slug": "applied-python-lab",
        "title": "Applied Python Lab",
        "code": "TT-PY-311",
        "mentor": "Michael Bassey",
        "cohort": "May 2026 Weekend Cohort",
        "schedule": "Saturday at 11:00 AM WAT",
        "course_duration": "4 weeks",
        "course_start_date": "2026-05-03",
        "hero_metric": "12 guided projects shipped",
        "description": "A production-minded classroom focused on pandas workflows, automation, notebooks, and reusable analysis pipelines.",
        "theme": "python",
        "completion": 71,
        "live_sessions": 1,
        "announcements": [
            {
                "kind": "announcement",
                "title": "Environment setup guide updated",
                "body": "The setup guide now includes virtual environment activation, notebook tips, and file naming rules for your project folders.",
                "posted_at": "2026-04-30T11:15:00+01:00",
            },
            {
                "kind": "resource",
                "title": "Template notebook added",
                "body": "Start from the provided notebook structure so your analysis, explanations, and outputs stay consistent across submissions.",
                "posted_at": "2026-04-29T19:00:00+01:00",
            },
        ],
        "modules": [
            {
                "title": "Module 4: Cleaning with pandas",
                "status": "In Progress",
                "summary": "Handle missing values, duplicate records, and date parsing across messy business datasets.",
                "items": ["Code lab: null handling", "Reference sheet: pandas patterns", "Peer review prompt"],
            },
            {
                "title": "Module 5: Automation workflows",
                "status": "Opening Soon",
                "summary": "Turn repeatable analyst work into scripts and scheduled reports.",
                "items": ["Mini project: file watcher", "Practice dataset: logistics ops", "Mentor review rubric"],
            },
        ],
        "assignments": [
            {
                "title": "Weekly reporting automation",
                "description": "Build a Python script that ingests CSV exports, cleans nulls, and produces a summary table ready for stakeholder review.",
                "due_at": "2026-05-06T18:00:00+01:00",
            },
            {
                "title": "Notebook reflection",
                "description": "Explain your code choices and note two areas you would refactor after mentor feedback.",
                "due_at": "2026-05-08T20:00:00+01:00",
            },
        ],
    },
    {
        "slug": "ai-product-studio",
        "title": "AI Product Studio",
        "code": "TT-AI-402",
        "mentor": "Chidinma Ude",
        "cohort": "Executive AI Cohort",
        "schedule": "Tuesday and Thursday at 8:00 PM WAT",
        "course_duration": "4 weeks",
        "course_start_date": "2026-05-05",
        "hero_metric": "6 product prototypes under review",
        "description": "A mentor-led AI classroom where teams move from prompt strategy to prototype planning, evaluation, and delivery.",
        "theme": "ai",
        "completion": 63,
        "live_sessions": 2,
        "announcements": [
            {
                "kind": "announcement",
                "title": "Prototype review board opened",
                "body": "Upload your use-case brief and score it against feasibility, data access, adoption risk, and customer value before class.",
                "posted_at": "2026-05-01T14:00:00+01:00",
            },
            {
                "kind": "resource",
                "title": "Reading pack: evaluation and guardrails",
                "body": "Review the concise guide on output review, safety boundaries, and stakeholder sign-off before your design review.",
                "posted_at": "2026-04-29T16:00:00+01:00",
            },
        ],
        "modules": [
            {
                "title": "Module 3: AI use-case design",
                "status": "In Progress",
                "summary": "Define business problems, user journeys, and measurable outcomes before writing prompts or building demos.",
                "items": ["Strategy canvas", "Evaluation worksheet", "Team critique forum"],
            },
            {
                "title": "Module 4: Prototype sprint",
                "status": "Next Sprint",
                "summary": "Turn validated ideas into testable prototypes with delivery milestones.",
                "items": ["Sprint backlog", "Demo rubric", "Stakeholder feedback template"],
            },
        ],
        "assignments": [
            {
                "title": "AI workflow blueprint",
                "description": "Map the end-to-end workflow, human approvals, fallback paths, and evaluation checkpoints for your proposed assistant.",
                "due_at": "2026-05-07T21:00:00+01:00",
            },
            {
                "title": "Risk and guardrails memo",
                "description": "Write a one-page memo covering data risk, output review, and governance boundaries.",
                "due_at": "2026-05-09T18:00:00+01:00",
            },
            {
                "title": "Prototype walkthrough recording",
                "description": "Record a short walkthrough of your prototype and explain its intended workflow.",
                "due_at": "2026-05-12T19:00:00+01:00",
            },
        ],
    },
]

SEED_STUDENTS = [
    {"name": "Demo Student", "email": "student@tektutors.com.ng"},
    {"name": "Amina Bello", "email": "amina@tektutors.com.ng"},
    {"name": "Victor James", "email": "victor@tektutors.com.ng"},
    {"name": "Grace Peters", "email": "grace@tektutors.com.ng"},
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('student', 'mentor', 'admin', 'growth_associate', 'growth_manager', 'corporate_staff', 'finance')),
    is_approved INTEGER NOT NULL DEFAULT 0,
    is_suspended INTEGER NOT NULL DEFAULT 0,
    suspended_at TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classrooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    cohort TEXT NOT NULL,
    schedule TEXT NOT NULL,
    course_duration TEXT NOT NULL DEFAULT '4 weeks',
    course_start_date TEXT,
    theme TEXT NOT NULL,
    description TEXT NOT NULL,
    hero_metric TEXT NOT NULL,
    completion INTEGER NOT NULL DEFAULT 0,
    attendance_target INTEGER NOT NULL DEFAULT 8,
    live_sessions INTEGER NOT NULL DEFAULT 0,
    mentor_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (mentor_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(classroom_id, user_id),
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    UNIQUE(classroom_id, kind, title),
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id)
);

CREATE TABLE IF NOT EXISTS modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE(classroom_id, title),
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id)
);

CREATE TABLE IF NOT EXISTS module_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE(module_id, content),
    FOREIGN KEY (module_id) REFERENCES modules (id)
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    assignment_type TEXT NOT NULL DEFAULT 'assignment',
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    due_at TEXT NOT NULL,
    challenge_reminder_sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(classroom_id, title),
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id)
);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    uploader_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    material_url TEXT,
    stored_name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
    FOREIGN KEY (uploader_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted',
    submitted_at TEXT NOT NULL,
    grade TEXT,
    feedback TEXT,
    stored_name TEXT,
    original_name TEXT,
    relative_path TEXT,
    resubmission_allowed INTEGER NOT NULL DEFAULT 0,
    UNIQUE(assignment_id, student_id),
    FOREIGN KEY (assignment_id) REFERENCES assignments (id),
    FOREIGN KEY (student_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS project_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'suggested',
    mentor_note TEXT,
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
    FOREIGN KEY (student_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS attendance_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    session_date TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(classroom_id, title, session_date),
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
    FOREIGN KEY (created_by) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS attendance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'present',
    notes TEXT,
    marked_at TEXT NOT NULL,
    UNIQUE(session_id, student_id),
    FOREIGN KEY (session_id) REFERENCES attendance_sessions (id),
    FOREIGN KEY (student_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS course_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    milestone INTEGER NOT NULL CHECK (milestone IN (4, 7)),
    attendance_count INTEGER NOT NULL,
    course_experience INTEGER NOT NULL CHECK (course_experience BETWEEN 1 AND 5),
    course_content INTEGER NOT NULL CHECK (course_content BETWEEN 1 AND 5),
    mentor_methodology INTEGER NOT NULL CHECK (mentor_methodology BETWEEN 1 AND 5),
    mentor_experience INTEGER NOT NULL CHECK (mentor_experience BETWEEN 1 AND 5),
    mentor_knowledge INTEGER NOT NULL CHECK (mentor_knowledge BETWEEN 1 AND 5),
    lms_ease INTEGER NOT NULL CHECK (lms_ease BETWEEN 1 AND 5),
    worst_experience TEXT NOT NULL,
    best_experience TEXT NOT NULL,
    improvement_suggestion TEXT NOT NULL,
    score_total INTEGER NOT NULL,
    score_percentage INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    UNIQUE(classroom_id, student_id, milestone),
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
    FOREIGN KEY (student_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS certificate_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    approved_by INTEGER NOT NULL,
    approved_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by INTEGER,
    UNIQUE(classroom_id, student_id),
    FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
    FOREIGN KEY (student_id) REFERENCES users (id),
    FOREIGN KEY (approved_by) REFERENCES users (id),
    FOREIGN KEY (revoked_by) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS idx_enrollments_user_id ON enrollments(user_id);
CREATE INDEX IF NOT EXISTS idx_classrooms_mentor_id ON classrooms(mentor_id);
CREATE INDEX IF NOT EXISTS idx_submissions_student_id ON submissions(student_id);
CREATE INDEX IF NOT EXISTS idx_attendance_records_student_id ON attendance_records(student_id);
CREATE INDEX IF NOT EXISTS idx_live_sessions_classroom_id ON live_sessions(classroom_id);
"""


def timestamp_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def generate_temporary_password(length: int = 12) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email.strip()))


def is_valid_url(value: str) -> bool:
    parsed_url = urlparse(value.strip())
    return parsed_url.scheme in {"http", "https"} and bool(parsed_url.netloc)


def append_resource_link(text: str, resource_link: str, label: str = "Resource link") -> str:
    resource_link = resource_link.strip()
    if not resource_link:
        return text
    return f"{text.rstrip()}\n{label}: {resource_link}"


def client_ip_address() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def public_form_rate_limited(form_name: str, *, limit: int) -> bool:
    now = time.time()
    key = f"{form_name}:{client_ip_address()}"
    recent_requests = [
        item
        for item in PUBLIC_FORM_RATE_LIMITS[key]
        if now - item < PUBLIC_FORM_BLOCK_WINDOW_SECONDS
    ]
    PUBLIC_FORM_RATE_LIMITS[key] = recent_requests
    if len(recent_requests) >= limit:
        return True
    recent_requests.append(now)
    return False


def form_submitted_too_quickly(min_seconds: int = PUBLIC_FORM_MIN_SECONDS) -> bool:
    raw_started_at = request.form.get("form_started_at", "").strip()
    try:
        started_at = float(raw_started_at)
    except ValueError:
        return True
    return time.time() - started_at < min_seconds


def honeypot_triggered() -> bool:
    return any(request.form.get(field_name, "").strip() for field_name in ("company", "website", "url"))


def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return str(token)


def csrf_field() -> Markup:
    token = escape(get_csrf_token())
    return Markup(f'<input type="hidden" name="_csrf_token" value="{token}">')


def csrf_token_valid(submitted_token: str | None) -> bool:
    expected_token = session.get("_csrf_token")
    return bool(
        submitted_token
        and expected_token
        and secrets.compare_digest(str(submitted_token), str(expected_token))
    )


def too_many_links(*values: str, max_links: int = PUBLIC_FORM_MAX_LINKS) -> bool:
    link_count = sum(len(URL_PATTERN.findall(value or "")) for value in values)
    return link_count > max_links


def public_form_blocked(form_name: str, *, limit: int, min_seconds: int = PUBLIC_FORM_MIN_SECONDS) -> bool:
    if honeypot_triggered():
        app.logger.info("Blocked %s form honeypot submission from %s", form_name, client_ip_address())
        return True
    if form_submitted_too_quickly(min_seconds):
        app.logger.info("Blocked %s form fast submission from %s", form_name, client_ip_address())
        return True
    if public_form_rate_limited(form_name, limit=limit):
        app.logger.warning("Rate limited %s form submission from %s", form_name, client_ip_address())
        return True
    return False


def postgres_type_definition(definition: str) -> str:
    normalized = definition.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    normalized = normalized.replace("AUTOINCREMENT", "")
    return normalized


def postgres_query(query: str) -> tuple[str, bool]:
    sql = postgres_type_definition(query.strip())
    wants_lastrowid = False
    if re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\b", sql, flags=re.IGNORECASE):
        sql = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, flags=re.IGNORECASE)
        if not re.search(r"\bON\s+CONFLICT\b", sql, flags=re.IGNORECASE):
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    if re.match(r"^INSERT\s+INTO\b", sql, flags=re.IGNORECASE) and not re.search(r"\bRETURNING\b", sql, flags=re.IGNORECASE):
        sql = sql.rstrip().rstrip(";") + " RETURNING id"
        wants_lastrowid = True

    sql = sql.replace("%", "%%")
    return sql.replace("?", "%s"), wants_lastrowid


class DatabaseRow(dict):
    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def database_row(row: Any) -> Any:
    if row is None or isinstance(row, DatabaseRow):
        return row
    if isinstance(row, dict):
        return DatabaseRow(row)
    return row


class PostgresCursor:
    def __init__(self, cursor: Any, lastrowid: int | None = None) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> Any:
        return database_row(self._cursor.fetchone())

    def fetchall(self) -> list[Any]:
        return [database_row(row) for row in self._cursor.fetchall()]


class PostgresConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> PostgresCursor:
        sql, wants_lastrowid = postgres_query(query)
        cursor = self._connection.cursor()
        cursor.execute(sql, params)
        lastrowid = None
        if wants_lastrowid:
            row = cursor.fetchone()
            lastrowid = int(list(row.values())[0]) if row else None
        return PostgresCursor(cursor, lastrowid)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def database_config_source() -> str:
    # Check if system-wide environment variables are set and differ from local .env config
    if os.environ.get("DATABASE_URL") and os.environ.get("DATABASE_URL") != PROJECT_ENV.get("DATABASE_URL"):
        return "environment:DATABASE_URL"
    if os.environ.get("POSTGRES_URL") and os.environ.get("POSTGRES_URL") != PROJECT_ENV.get("POSTGRES_URL"):
        return "environment:POSTGRES_URL"
    if os.environ.get("POSTGRESQL_URL") and os.environ.get("POSTGRESQL_URL") != PROJECT_ENV.get("POSTGRESQL_URL"):
        return "environment:POSTGRESQL_URL"
    
    # Fallback to local .env file settings if present
    if PROJECT_ENV.get("DATABASE_URL"):
        return ".env:DATABASE_URL"
    if PROJECT_ENV.get("POSTGRES_URL"):
        return ".env:POSTGRES_URL"
    if PROJECT_ENV.get("POSTGRESQL_URL"):
        return ".env:POSTGRESQL_URL"
    
    # Fallback to general system environment variables
    if os.environ.get("DATABASE_URL"):
        return "environment:DATABASE_URL"
    if os.environ.get("POSTGRES_URL"):
        return "environment:POSTGRES_URL"
    if os.environ.get("POSTGRESQL_URL"):
        return "environment:POSTGRESQL_URL"
    return "sqlite fallback"


def database_config_summary() -> str:
    if not DATABASE_URL:
        return f"engine=sqlite file={DATABASE_FILE} source={database_config_source()}"

    parsed = urlparse(DATABASE_URL)
    host = parsed.hostname or "unknown"
    port = parsed.port or 5432
    database_name = parsed.path.lstrip("/") or "unknown"
    return f"engine=postgresql host={host} port={port} database={database_name} source={database_config_source()}"


def connect_db() -> Any:
    if DATABASE_ENGINE == "postgresql":
        app.logger.debug("Opening database connection: %s", database_config_summary())
        if psycopg is None or dict_row is None:
            raise RuntimeError("PostgreSQL support requires psycopg[binary]. Install requirements.txt first.")
        connection = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=DATABASE_CONNECT_TIMEOUT_SECONDS,
        )
        return PostgresConnection(connection)

    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def schema_statements(schema_sql: str) -> list[str]:
    return [statement.strip() for statement in schema_sql.split(";") if statement.strip()]


def initialize_database_schema(db: Any) -> None:
    if DATABASE_ENGINE == "postgresql":
        for statement in schema_statements(postgres_type_definition(SCHEMA_SQL)):
            db.execute(statement)
        return
    db.executescript(SCHEMA_SQL)


def ensure_column(db: Any, table_name: str, column_name: str, definition: str) -> None:
    if DATABASE_ENGINE == "postgresql":
        existing = db.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ? AND column_name = ?
            """,
            (table_name, column_name),
        ).fetchone()
        if existing is None:
            db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {postgres_type_definition(definition)}")
        return

    existing_columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in existing_columns:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def ensure_admin_role_supported(db: Any) -> None:
    if DATABASE_ENGINE == "postgresql":
        return

    table_sql_row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    table_sql = (table_sql_row["sql"] or "") if table_sql_row else ""
    if "'growth_associate'" in table_sql:
        return

    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("ALTER TABLE users RENAME TO users_legacy")
    db.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('student', 'mentor', 'admin', 'growth_associate', 'growth_manager', 'corporate_staff', 'finance')),
            is_approved INTEGER NOT NULL DEFAULT 0,
            is_suspended INTEGER NOT NULL DEFAULT 0,
            suspended_at TEXT,
            approved_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO users (id, name, email, password_hash, role, is_approved, is_suspended, suspended_at, approved_at, created_at)
        SELECT id, name, email, password_hash, role, COALESCE(is_approved, 1), 0, NULL, approved_at, created_at
        FROM users_legacy
        """
    )
    db.execute("DROP TABLE users_legacy")
    db.execute("PRAGMA foreign_keys = ON")


def migrate_database_schema(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            classroom_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            session_date TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(classroom_id, title, session_date),
            FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'present',
            notes TEXT,
            marked_at TEXT NOT NULL,
            UNIQUE(session_id, student_id),
            FOREIGN KEY (session_id) REFERENCES attendance_sessions (id),
            FOREIGN KEY (student_id) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS course_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            classroom_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            milestone INTEGER NOT NULL CHECK (milestone IN (4, 7)),
            attendance_count INTEGER NOT NULL,
            course_experience INTEGER NOT NULL CHECK (course_experience BETWEEN 1 AND 5),
            course_content INTEGER NOT NULL CHECK (course_content BETWEEN 1 AND 5),
            mentor_methodology INTEGER NOT NULL CHECK (mentor_methodology BETWEEN 1 AND 5),
            mentor_experience INTEGER NOT NULL CHECK (mentor_experience BETWEEN 1 AND 5),
            mentor_knowledge INTEGER NOT NULL CHECK (mentor_knowledge BETWEEN 1 AND 5),
            lms_ease INTEGER NOT NULL CHECK (lms_ease BETWEEN 1 AND 5),
            worst_experience TEXT NOT NULL,
            best_experience TEXT NOT NULL,
            improvement_suggestion TEXT NOT NULL,
            score_total INTEGER NOT NULL,
            score_percentage INTEGER NOT NULL,
            submitted_at TEXT NOT NULL,
            UNIQUE(classroom_id, student_id, milestone),
            FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
            FOREIGN KEY (student_id) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS live_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            classroom_id INTEGER NOT NULL,
            meeting_key TEXT UNIQUE,
            topic TEXT NOT NULL,
            agenda TEXT,
            start_time TEXT NOT NULL,
            duration INTEGER NOT NULL,
            join_link TEXT NOT NULL,
            start_link TEXT NOT NULL,
            recording_download_url TEXT,
            recording_play_url TEXT,
            recording_status TEXT DEFAULT 'pending',
            reminder_sent INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (classroom_id) REFERENCES classrooms (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS project_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            classroom_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'suggested',
            mentor_note TEXT,
            FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
            FOREIGN KEY (student_id) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS certificate_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            classroom_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            approved_by INTEGER NOT NULL,
            approved_at TEXT NOT NULL,
            revoked_at TEXT,
            revoked_by INTEGER,
            UNIQUE(classroom_id, student_id),
            FOREIGN KEY (classroom_id) REFERENCES classrooms (id),
            FOREIGN KEY (student_id) REFERENCES users (id),
            FOREIGN KEY (approved_by) REFERENCES users (id),
            FOREIGN KEY (revoked_by) REFERENCES users (id)
        )
        """
    )
    ensure_column(db, "assignments", "assignment_type", "TEXT NOT NULL DEFAULT 'assignment'")
    ensure_column(db, "assignments", "challenge_reminder_sent", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "users", "is_approved", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "users", "is_suspended", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "users", "suspended_at", "TEXT")
    ensure_column(db, "users", "approved_at", "TEXT")
    ensure_column(db, "classrooms", "attendance_target", "INTEGER NOT NULL DEFAULT 8")
    ensure_column(db, "classrooms", "course_duration", "TEXT NOT NULL DEFAULT '4 weeks'")
    ensure_column(db, "classrooms", "course_start_date", "TEXT")
    ensure_column(db, "live_sessions", "reminder_sent", "INTEGER NOT NULL DEFAULT 0")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_associate_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            phone_number TEXT NOT NULL,
            whatsapp_number TEXT,
            location TEXT NOT NULL,
            country TEXT NOT NULL DEFAULT 'Nigeria',
            communication_channel TEXT NOT NULL DEFAULT 'Email',
            current_occupation TEXT NOT NULL,
            company_name TEXT,
            linkedin_url TEXT,
            network_areas TEXT,
            access_industries TEXT,
            monthly_prospect_reach TEXT,
            agreed_to_terms INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            bank_name TEXT,
            account_number TEXT,
            account_name TEXT,
            payout_notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS prospects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referral_id TEXT NOT NULL UNIQUE,
            growth_associate_id INTEGER NOT NULL,
            full_name TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            whatsapp_number TEXT,
            email TEXT NOT NULL,
            location TEXT,
            country TEXT DEFAULT 'Nigeria',
            organization TEXT,
            job_title TEXT,
            preferred_contact_method TEXT DEFAULT 'Phone Call',
            opportunity_type TEXT NOT NULL,
            training_interests TEXT NOT NULL,
            prospect_details TEXT,
            lead_temperature TEXT DEFAULT 'Warm',
            estimated_value REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'Submitted',
            is_duplicate INTEGER DEFAULT 0,
            duplicate_notes TEXT,
            submitted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (growth_associate_id) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS follow_up_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id INTEGER NOT NULL,
            growth_associate_id INTEGER NOT NULL,
            preferred_contact_method TEXT NOT NULL,
            preferred_contact_time TEXT NOT NULL,
            reason TEXT,
            additional_instructions TEXT,
            assigned_staff_id INTEGER,
            status TEXT NOT NULL DEFAULT 'Requested',
            internal_notes TEXT,
            next_follow_up_date TEXT,
            requested_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (prospect_id) REFERENCES prospects (id),
            FOREIGN KEY (growth_associate_id) REFERENCES users (id),
            FOREIGN KEY (assigned_staff_id) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS corporate_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            growth_associate_id INTEGER NOT NULL,
            organization_name TEXT NOT NULL,
            organization_type TEXT NOT NULL,
            website TEXT,
            location TEXT,
            contact_person TEXT NOT NULL,
            contact_position TEXT,
            contact_phone TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            training_topic TEXT NOT NULL,
            skills_required TEXT,
            participant_count INTEGER DEFAULT 1,
            training_level TEXT DEFAULT 'Intermediate',
            delivery_mode TEXT DEFAULT 'Online',
            preferred_date TEXT,
            expected_duration TEXT,
            training_objectives TEXT,
            additional_requirements TEXT,
            identification_source TEXT,
            expressed_interest TEXT,
            meeting_taken TEXT,
            existing_budget TEXT,
            estimated_budget REAL DEFAULT 0.0,
            expected_decision_date TEXT,
            proposal_requested INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Submitted',
            submitted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (growth_associate_id) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS proposal_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corporate_opportunity_id INTEGER,
            prospect_id INTEGER,
            growth_associate_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            requirements TEXT,
            status TEXT NOT NULL DEFAULT 'Requested',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (corporate_opportunity_id) REFERENCES corporate_opportunities (id),
            FOREIGN KEY (prospect_id) REFERENCES prospects (id),
            FOREIGN KEY (growth_associate_id) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_request_id INTEGER NOT NULL,
            growth_associate_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            stored_name TEXT NOT NULL,
            original_name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Ready',
            created_at TEXT NOT NULL,
            FOREIGN KEY (proposal_request_id) REFERENCES proposal_requests (id),
            FOREIGN KEY (growth_associate_id) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id INTEGER NOT NULL UNIQUE,
            growth_associate_id INTEGER NOT NULL,
            commission_amount REAL DEFAULT 0.0,
            commission_status TEXT NOT NULL DEFAULT 'Pending',
            payment_approval_status TEXT NOT NULL DEFAULT 'Unapproved',
            approved_by INTEGER,
            approved_at TEXT,
            paid_at TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (prospect_id) REFERENCES prospects (id),
            FOREIGN KEY (growth_associate_id) REFERENCES users (id),
            FOREIGN KEY (approved_by) REFERENCES users (id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            previous_value TEXT,
            new_value TEXT,
            ip_address TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )
    ensure_admin_role_supported(db)
    ensure_column(db, "users", "must_change_password", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "users", "reset_token", "TEXT")
    ensure_column(db, "users", "reset_token_expires_at", "TEXT")
    ensure_column(db, "materials", "material_url", "TEXT")
    ensure_column(db, "submissions", "stored_name", "TEXT")
    ensure_column(db, "submissions", "original_name", "TEXT")
    ensure_column(db, "submissions", "relative_path", "TEXT")
    ensure_column(db, "submissions", "resubmission_allowed", "INTEGER NOT NULL DEFAULT 0")
    db.execute("UPDATE users SET is_approved = 1 WHERE is_approved IS NULL")
    db.execute("UPDATE users SET is_suspended = 0 WHERE is_suspended IS NULL")
    db.execute("UPDATE users SET must_change_password = 0 WHERE must_change_password IS NULL")
    db.execute("UPDATE classrooms SET attendance_target = 8 WHERE attendance_target IS NULL OR attendance_target <= 0")
    db.execute("UPDATE classrooms SET course_duration = '4 weeks' WHERE course_duration IS NULL OR TRIM(course_duration) = ''")
    db.execute("UPDATE classrooms SET course_start_date = SUBSTR(created_at, 1, 10) WHERE course_start_date IS NULL OR TRIM(course_start_date) = ''")
    db.execute("UPDATE submissions SET resubmission_allowed = 0 WHERE resubmission_allowed IS NULL")
    db.execute(
        """
        UPDATE assignments
        SET assignment_type = CASE
            WHEN LOWER(title || ' ' || COALESCE(description, '')) LIKE '%quiz%'
              OR LOWER(title || ' ' || COALESCE(description, '')) LIKE '%test%' THEN 'quiz'
            WHEN LOWER(title || ' ' || COALESCE(description, '')) LIKE '%project%'
              OR LOWER(title || ' ' || COALESCE(description, '')) LIKE '%capstone%'
              OR LOWER(title || ' ' || COALESCE(description, '')) LIKE '%portfolio%' THEN 'project'
            WHEN LOWER(title || ' ' || COALESCE(description, '')) LIKE '%challenge%'
              OR LOWER(title || ' ' || COALESCE(description, '')) LIKE '%checkpoint%'
              OR LOWER(title || ' ' || COALESCE(description, '')) LIKE '%brief%' THEN 'challenge'
            ELSE 'assignment'
        END
        WHERE assignment_type IS NULL OR assignment_type = ''
        """
    )
    db.execute(
        """
        UPDATE assignments
        SET assignment_type = 'project'
        WHERE assignment_type IN ('assignment', 'challenge')
          AND (
            LOWER(title || ' ' || COALESCE(description, '')) LIKE '%project%'
            OR LOWER(title || ' ' || COALESCE(description, '')) LIKE '%capstone%'
            OR LOWER(title || ' ' || COALESCE(description, '')) LIKE '%portfolio%'
          )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_enrollments_user_id ON enrollments(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_classrooms_mentor_id ON classrooms(mentor_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_submissions_student_id ON submissions(student_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_attendance_records_student_id ON attendance_records(student_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_live_sessions_classroom_id ON live_sessions(classroom_id)")


def allowed_upload_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS


def parse_notification_recipients(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [normalize_email(item) for item in raw_value.split(",") if item.strip()]


def mail_delivery_configured() -> bool:
    sender = bool(app.config.get("MAIL_DEFAULT_SENDER"))
    server = bool(app.config.get("MAIL_SERVER"))
    port = bool(app.config.get("MAIL_PORT"))
    username = app.config.get("MAIL_USERNAME")
    password = app.config.get("MAIL_PASSWORD")
    credentials_configured = (not username and not password) or (bool(username) and bool(password))
    return sender and server and port and credentials_configured


def ensure_mail_delivery_configured() -> None:
    if not app.config.get("MAIL_DEFAULT_SENDER"):
        raise RuntimeError("Email sending is not configured. Set MAIL_DEFAULT_SENDER for this deployment.")

    username = app.config.get("MAIL_USERNAME")
    password = app.config.get("MAIL_PASSWORD")
    if bool(username) != bool(password):
        raise RuntimeError("Email sending is not configured correctly. Set both MAIL_USERNAME and MAIL_PASSWORD, or leave both empty for a local SMTP relay.")

    if not app.config.get("MAIL_SERVER") or not app.config.get("MAIL_PORT"):
        raise RuntimeError("Email sending is not configured. Set MAIL_SERVER and MAIL_PORT for this deployment.")


def form_notifications_configured() -> bool:
    return mail_delivery_configured() and bool(parse_notification_recipients(FORMS_NOTIFICATION_EMAIL))


def send_form_notification(*, subject: str, body: str) -> None:
    if not form_notifications_configured():
        if REQUIRE_FORM_EMAIL_DELIVERY:
            raise RuntimeError("Email notifications are not configured on this deployment.")
        return

    try:
        message = Message(
            subject=subject,
            recipients=parse_notification_recipients(FORMS_NOTIFICATION_EMAIL),
            body=body,
            sender=app.config["MAIL_DEFAULT_SENDER"],
        )
        mail.send(message)
    except Exception as exc:
        if REQUIRE_FORM_EMAIL_DELIVERY:
            raise RuntimeError("Email notification could not be delivered. Please try again shortly.") from exc
        app.logger.error(f"Failed to send form notification: {exc}")


def send_registration_acknowledgement_email(
    *,
    recipient_name: str,
    recipient_email: str,
    preferred_course: str,
) -> bool:
    if not mail_delivery_configured():
        if REQUIRE_FORM_EMAIL_DELIVERY:
            raise RuntimeError("Registration acknowledgement email is not configured on this deployment.")
        return False

    body = (
        f"Dear {recipient_name},\n\n"
        "Thank you for registering with Tektutors.\n\n"
        "This email confirms that your registration form has been received and your registration is complete. "
        "Our team will review your submission and communicate the next steps to you soon.\n\n"
        f"Selected course: {preferred_course}\n\n"
        "Best regards,\n"
        "Tektutors"
    )

    try:
        message = Message(
            subject="Tektutors Registration Confirmation",
            recipients=[recipient_email],
            body=body,
            sender=app.config["MAIL_DEFAULT_SENDER"],
        )
        mail.send(message)
        return True
    except Exception as exc:
        if REQUIRE_FORM_EMAIL_DELIVERY:
            raise RuntimeError("Registration acknowledgement email could not be delivered. Please try again shortly.") from exc
        app.logger.error(f"Failed to send registration acknowledgement to {recipient_email}: {exc}")
        return False


def validate_production_configuration() -> None:
    if not IS_PRODUCTION:
        return

    errors: list[str] = []

    if not secret_key or len(secret_key) < 32 or secret_key == "change-this-to-a-long-random-secret":
        errors.append("SECRET_KEY must be a unique random value of at least 32 characters.")

    if not DEFAULT_ADMIN_PASSWORD or DEFAULT_ADMIN_PASSWORD == DEFAULT_DEMO_PASSWORD or "change-this" in DEFAULT_ADMIN_PASSWORD.lower():
        errors.append("ADMIN_PASSWORD must be set to a strong production-only password.")

    if DATABASE_ENGINE != "postgresql" and not ALLOW_SQLITE_IN_PRODUCTION:
        errors.append("DATABASE_URL must be set for PostgreSQL, or ALLOW_SQLITE_IN_PRODUCTION=true must be explicitly configured.")

    if REQUIRE_FORM_EMAIL_DELIVERY and not form_notifications_configured():
        errors.append("Mail credentials and FORMS_NOTIFICATION_EMAIL must be configured when form email delivery is required.")

    if errors:
        raise RuntimeError("Production configuration is incomplete: " + " ".join(errors))


validate_production_configuration()


def submission_locked(submission: Any | dict[str, Any] | None) -> bool:
    if not submission:
        return False
    if bool(submission.get("resubmission_allowed")):
        return False

    status = (submission.get("submission_status") or submission.get("status") or "").strip().lower()
    return bool(submission.get("grade") or submission.get("feedback") or status in {"reviewed", "approved"})


def store_uploaded_file(
    file_storage: Any,
    target_root: Path,
    slug_prefix: str,
    allowed_extensions: set[str] | None = None,
) -> dict[str, str]:
    original_name = secure_filename(file_storage.filename or "")
    if not original_name:
        raise ValueError("Please choose a file to upload.")
    allowed = allowed_extensions or ALLOWED_UPLOAD_EXTENSIONS
    if Path(original_name).suffix.lower() not in allowed:
        allowed_list = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported file type. Allowed types: {allowed_list}.")

    extension = Path(original_name).suffix.lower()
    stem = secure_filename(Path(original_name).stem) or "file"
    unique_name = f"{slug_prefix}_{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S%f')}_{stem}{extension}"
    target_root.mkdir(parents=True, exist_ok=True)
    destination = target_root / unique_name
    file_storage.save(destination)
    return {
        "stored_name": unique_name,
        "original_name": original_name,
        "relative_path": str(destination.relative_to(UPLOADS_DIR)).replace("\\", "/"),
    }


def delete_uploaded_file(relative_path: str | None) -> None:
    if not relative_path:
        return

    file_path = (UPLOADS_DIR / relative_path).resolve()
    uploads_root = UPLOADS_DIR.resolve()
    if uploads_root not in file_path.parents:
        return

    if file_path.exists():
        try:
            file_path.unlink()
        except OSError:
            pass


def ensure_demo_material_file(classroom_slug: str, filename: str, content: str) -> dict[str, str]:
    target_dir = MATERIALS_DIR / classroom_slug
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = secure_filename(filename)
    destination = target_dir / safe_name
    if not destination.exists():
        destination.write_text(content, encoding="utf-8")
    return {
        "stored_name": safe_name,
        "original_name": filename,
        "relative_path": str(destination.relative_to(UPLOADS_DIR)).replace("\\", "/"),
    }


def get_db() -> Any:
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(exception: BaseException | None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = get_db().execute(query, params).fetchone()
    return dict(row) if row else None


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    rows = get_db().execute(query, params).fetchall()
    return [dict(row) for row in rows]


def collect_csv_emails(file_path: Path, email_field: str = "email") -> set[str]:
    if not file_path.exists():
        return set()

    emails: set[str] = set()
    with file_path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            email = normalize_email(row.get(email_field, ""))
            if is_valid_email(email):
                emails.add(email)
    return emails


def collect_bulk_email_recipients() -> list[str]:
    recipients = {
        normalize_email(row["email"])
        for row in fetch_all("SELECT email FROM users WHERE email IS NOT NULL AND email != ''")
        if is_valid_email(row["email"])
    }
    for file_path in (CONTACT_FILE, NEWSLETTER_FILE, REGISTRATION_FILE, CAREERS_FILE):
        recipients.update(collect_csv_emails(file_path))
    return sorted(recipients)


def mentor_email_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")
    return f"{slug}@tektutors.com.ng"


def send_login_details_email(*, recipient_name: str, recipient_email: str, password: str, role: str, login_url: str) -> None:
    ensure_mail_delivery_configured()

    subject = "Your Tektutors Learning Hub Login Details"
    body = (
        f"Hello {recipient_name},\n\n"
        f"Your registration has been reviewed and approved for Tektutors {role} Learning Hub access.\n\n"
        f"Login URL: {login_url}\n"
        f"Email: {recipient_email}\n"
        f"Temporary Password: {password}\n\n"
        "Please sign in and change this password after your first login if your workflow requires it.\n\n"
        "Best regards,\n"
        "Tektutors Admin"
    )
    try:
        with app.app_context():
            message = Message(
                subject=subject,
                recipients=[recipient_email],
                body=body,
                sender=app.config["MAIL_DEFAULT_SENDER"],
            )
            mail.send(message)
    except Exception as exc:
        app.logger.exception("Failed to send login details email to %s", recipient_email)
        raise RuntimeError("The account was created, but the login email could not be delivered. Check the mail settings and try sending it again.") from exc


def send_password_reset_email(*, recipient_name: str, recipient_email: str, reset_url: str) -> None:
    ensure_mail_delivery_configured()

    subject = "Reset Your Tektutors Learning Hub Password"
    body = (
        f"Hello {recipient_name},\n\n"
        "We received a request to reset your Tektutors Learning Hub password.\n\n"
        f"Reset your password here: {reset_url}\n\n"
        "This link expires in 30 minutes. If you did not request a reset, you can ignore this email.\n\n"
        "Best regards,\n"
        "Tektutors Admin"
    )
    try:
        with app.app_context():
            message = Message(
                subject=subject,
                recipients=[recipient_email],
                body=body,
                sender=app.config["MAIL_DEFAULT_SENDER"],
            )
            mail.send(message)
    except Exception as exc:
        app.logger.exception("Failed to send password reset email to %s", recipient_email)
        raise RuntimeError("Password reset email could not be delivered right now. Please try again later.") from exc


def send_submission_notification_email(
    *,
    mentor_name: str,
    mentor_email: str,
    learner_name: str,
    classroom_title: str,
    coursework_title: str,
    coursework_type: str,
    submission_url: str,
    review_url: str,
) -> None:
    ensure_mail_delivery_configured()

    coursework_label = coursework_type.replace("_", " ").title()
    subject = f"New {coursework_label} Submission: {coursework_title}"
    body = (
        f"Hello {mentor_name},\n\n"
        f"{learner_name} has submitted {coursework_label.lower()} coursework in {classroom_title}.\n\n"
        f"Coursework: {coursework_title}\n"
        f"Submission link: {submission_url}\n"
        f"Review in the Learning Hub: {review_url}\n\n"
        "Best regards,\n"
        "Tektutors Learning Hub"
    )
    try:
        with app.app_context():
            message = Message(
                subject=subject,
                recipients=[mentor_email],
                body=body,
                sender=app.config["MAIL_DEFAULT_SENDER"],
            )
            mail.send(message)
    except Exception as exc:
        app.logger.exception("Failed to send submission notification email to %s", mentor_email)
        raise RuntimeError("The submission was saved, but the mentor notification email could not be delivered.") from exc


def send_project_suggestion_email(
    *,
    mentor_name: str,
    mentor_email: str,
    learner_name: str,
    classroom_title: str,
    project_title: str,
    project_description: str,
    review_url: str,
) -> None:
    ensure_mail_delivery_configured()

    subject = f"New Project Suggestion: {project_title}"
    body = (
        f"Hello {mentor_name},\n\n"
        f"{learner_name} suggested a project for {classroom_title}.\n\n"
        f"Project title: {project_title}\n"
        f"Project idea:\n{project_description}\n\n"
        f"Review in the Learning Hub: {review_url}\n\n"
        "Best regards,\n"
        "Tektutors Learning Hub"
    )
    try:
        with app.app_context():
            message = Message(
                subject=subject,
                recipients=[mentor_email],
                body=body,
                sender=app.config["MAIL_DEFAULT_SENDER"],
            )
            mail.send(message)
    except Exception as exc:
        app.logger.exception("Failed to send project suggestion email to %s", mentor_email)
        raise RuntimeError("The suggestion was saved, but the mentor notification email could not be delivered.") from exc


def send_project_suggestion_review_email(
    *,
    student_name: str,
    student_email: str,
    classroom_title: str,
    project_title: str,
    status: str,
    mentor_note: str | None,
    classroom_url: str,
) -> None:
    ensure_mail_delivery_configured()

    subject = f"Project Suggestion Reviewed: {project_title}"
    note_part = f"\nMentor note:\n{mentor_note}\n" if mentor_note else ""
    body = (
        f"Hello {student_name},\n\n"
        f"Your project suggestion for {classroom_title} has been reviewed.\n\n"
        f"Project title: {project_title}\n"
        f"Status: {status.title()}\n"
        f"{note_part}\n"
        f"View details in the Learning Hub: {classroom_url}\n\n"
        "Best regards,\n"
        "Tektutors Learning Hub"
    )
    try:
        with app.app_context():
            message = Message(
                subject=subject,
                recipients=[student_email],
                body=body,
                sender=app.config["MAIL_DEFAULT_SENDER"],
            )
            mail.send(message)
    except Exception as exc:
        app.logger.exception("Failed to send project suggestion review email to %s", student_email)
        raise RuntimeError("The review was saved, but the learner notification email could not be delivered.") from exc



def send_classroom_learner_notification_email(
    *,
    classroom_id: int,
    classroom_title: str,
    item_title: str,
    item_type: str,
    message_body: str,
    action_url: str,
) -> int:
    learners = fetch_all(
        """
        SELECT name, email
        FROM users u
        JOIN enrollments e ON e.user_id = u.id
        WHERE
            e.classroom_id = ?
            AND u.role = 'student'
            AND u.is_approved = 1
            AND COALESCE(u.is_suspended, 0) = 0
        ORDER BY u.name ASC
        """,
        (classroom_id,),
    )
    if not learners:
        return 0

    ensure_mail_delivery_configured()

    item_label = item_type.replace("_", " ").title()
    subject = f"New {item_label} in {classroom_title}: {item_title}"
    try:
        with app.app_context():
            with mail.connect() as connection:
                for learner in learners:
                    body = (
                        f"Hello {learner['name']},\n\n"
                        f"A new {item_label.lower()} has been posted in {classroom_title}.\n\n"
                        f"Title: {item_title}\n"
                        f"{message_body}\n\n"
                        f"Open in the Learning Hub: {action_url}\n\n"
                        "Best regards,\n"
                        "Tektutors Learning Hub"
                    )
                    message = Message(
                        subject=subject,
                        recipients=[learner["email"]],
                        body=body,
                        sender=app.config["MAIL_DEFAULT_SENDER"],
                    )
                    connection.send(message)
    except Exception as exc:
        app.logger.exception("Failed to send classroom learner notification email for classroom %s", classroom_id)
        raise RuntimeError("The item was saved, but learner notification emails could not be delivered.") from exc

    return len(learners)


def ensure_user(
    db: Any,
    *,
    name: str,
    email: str,
    role: str,
    password: str = DEFAULT_DEMO_PASSWORD,
    force_password_change: bool = False,
) -> int:
    normalized_email = normalize_email(email)
    existing = db.execute("SELECT id FROM users WHERE email = ?", (normalized_email,)).fetchone()
    if existing:
        db.execute(
            """
            UPDATE users
            SET name = ?, role = ?, is_approved = 1, approved_at = COALESCE(approved_at, ?), must_change_password = ?
            WHERE id = ?
            """,
            (name, role, timestamp_now(), int(force_password_change), int(existing["id"])),
        )
        return int(existing["id"])

    cursor = db.execute(
        """
        INSERT INTO users (name, email, password_hash, role, is_approved, approved_at, created_at, must_change_password)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            normalized_email,
            generate_password_hash(password),
            role,
            1,
            timestamp_now(),
            timestamp_now(),
            int(force_password_change),
        ),
    )
    return int(cursor.lastrowid)


def seed_database(db: Any) -> None:
    if IS_PRODUCTION and not DEFAULT_ADMIN_PASSWORD:
        raise RuntimeError("ADMIN_PASSWORD must be set when APP_ENV=production.")

    admin_password = DEFAULT_ADMIN_PASSWORD or DEFAULT_DEMO_PASSWORD
    ensure_user(
        db,
        name="Tektutors Admin",
        email=DEFAULT_ADMIN_EMAIL,
        role="admin",
        password=admin_password,
        force_password_change=not bool(DEFAULT_ADMIN_PASSWORD),
    )

    demo_student_ids: list[int] = []
    if not IS_PRODUCTION:
        for student in SEED_STUDENTS:
            demo_student_ids.append(
                ensure_user(db, name=student["name"], email=student["email"], role="student", password=DEFAULT_DEMO_PASSWORD)
            )

    for classroom in LMS_CLASSROOMS:
        mentor_password = generate_temporary_password() if IS_PRODUCTION else DEFAULT_DEMO_PASSWORD
        mentor_id = ensure_user(
            db,
            name=classroom["mentor"],
            email=mentor_email_from_name(classroom["mentor"]),
            role="mentor",
            password=mentor_password,
            force_password_change=IS_PRODUCTION,
        )
        db.execute(
            """
            INSERT INTO classrooms (
                slug, title, code, cohort, schedule, course_duration, course_start_date, theme, description,
                hero_metric, completion, attendance_target, live_sessions, mentor_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                title = excluded.title,
                code = excluded.code,
                cohort = excluded.cohort,
                schedule = excluded.schedule,
                course_duration = excluded.course_duration,
                course_start_date = excluded.course_start_date,
                theme = excluded.theme,
                description = excluded.description,
                hero_metric = excluded.hero_metric,
                completion = excluded.completion,
                attendance_target = COALESCE(classrooms.attendance_target, excluded.attendance_target),
                live_sessions = excluded.live_sessions,
                mentor_id = excluded.mentor_id
            """,
            (
                classroom["slug"],
                classroom["title"],
                classroom["code"],
                classroom["cohort"],
                classroom["schedule"],
                classroom.get("course_duration", "4 weeks"),
                classroom.get("course_start_date"),
                classroom["theme"],
                classroom["description"],
                classroom["hero_metric"],
                classroom["completion"],
                classroom.get("attendance_target", 8),
                classroom["live_sessions"],
                mentor_id,
                timestamp_now(),
            ),
        )
        classroom_row = db.execute("SELECT id FROM classrooms WHERE slug = ?", (classroom["slug"],)).fetchone()
        classroom_id = int(classroom_row["id"])

        for student_id in demo_student_ids[:2]:
            db.execute(
                "INSERT OR IGNORE INTO enrollments (classroom_id, user_id, created_at) VALUES (?, ?, ?)",
                (classroom_id, student_id, timestamp_now()),
            )

        if len(demo_student_ids) > 2 and classroom["slug"] == "data-analysis-bootcamp":
            db.execute(
                "INSERT OR IGNORE INTO enrollments (classroom_id, user_id, created_at) VALUES (?, ?, ?)",
                (classroom_id, demo_student_ids[2], timestamp_now()),
            )
        if len(demo_student_ids) > 3 and classroom["slug"] == "ai-product-studio":
            db.execute(
                "INSERT OR IGNORE INTO enrollments (classroom_id, user_id, created_at) VALUES (?, ?, ?)",
                (classroom_id, demo_student_ids[3], timestamp_now()),
            )

        for announcement in classroom["announcements"]:
            db.execute(
                """
                INSERT OR IGNORE INTO announcements (classroom_id, kind, title, body, posted_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    classroom_id,
                    announcement["kind"],
                    announcement["title"],
                    announcement["body"],
                    announcement["posted_at"],
                ),
            )

        for module_index, module in enumerate(classroom["modules"], start=1):
            db.execute(
                """
                INSERT OR IGNORE INTO modules (classroom_id, title, status, summary, position)
                VALUES (?, ?, ?, ?, ?)
                """,
                (classroom_id, module["title"], module["status"], module["summary"], module_index),
            )
            module_row = db.execute(
                "SELECT id FROM modules WHERE classroom_id = ? AND title = ?",
                (classroom_id, module["title"]),
            ).fetchone()
            module_id = int(module_row["id"])
            for item_index, item in enumerate(module["items"], start=1):
                db.execute(
                    """
                    INSERT OR IGNORE INTO module_items (module_id, content, position)
                    VALUES (?, ?, ?)
                    """,
                    (module_id, item, item_index),
                )

        for assignment in classroom["assignments"]:
            db.execute(
                """
                INSERT OR IGNORE INTO assignments (classroom_id, assignment_type, title, description, due_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    classroom_id,
                    infer_assignment_type(assignment["title"], assignment["description"]),
                    assignment["title"],
                    assignment["description"],
                    assignment["due_at"],
                    timestamp_now(),
                ),
            )

        material_file = ensure_demo_material_file(
            classroom["slug"],
            f"{classroom['code'].lower()}-welcome-guide.txt",
            "\n".join(
                [
                    classroom["title"],
                    "",
                    "Welcome to your Tektutors classroom.",
                    f"Cohort: {classroom['cohort']}",
                    f"Schedule: {classroom['schedule']}",
                    "",
                    "Inside this file you'll find orientation notes, weekly expectations, and classroom workflow guidance.",
                ]
            ),
        )
        existing_material = db.execute(
            "SELECT id FROM materials WHERE classroom_id = ? AND title = ?",
            (classroom_id, "Welcome guide"),
        ).fetchone()
        if existing_material is None:
            db.execute(
                """
                INSERT INTO materials (
                    classroom_id, uploader_id, title, description, stored_name, original_name, relative_path, uploaded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    classroom_id,
                    mentor_id,
                    "Welcome guide",
                    "Orientation notes and weekly expectations for the cohort.",
                    material_file["stored_name"],
                    material_file["original_name"],
                    material_file["relative_path"],
                    timestamp_now(),
                ),
            )

    first_assignment = db.execute(
        """
        SELECT a.id
        FROM assignments a
        JOIN classrooms c ON c.id = a.classroom_id
        WHERE c.slug = 'data-analysis-bootcamp'
        ORDER BY a.due_at ASC
        LIMIT 1
        """
    ).fetchone()
    if first_assignment and demo_student_ids:
        db.execute(
            """
            INSERT OR IGNORE INTO submissions (
                assignment_id, student_id, content, status, submitted_at, grade, feedback
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(first_assignment["id"]),
                demo_student_ids[0],
                "Uploaded sales cleanup workbook with issue notes and chart recommendations.",
                "submitted",
                "2026-05-01T16:15:00+01:00",
                "A-",
                "Great structure. Tighten your notes on duplicate handling before the live review.",
            ),
        )


def initialize_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MATERIALS_DIR.mkdir(parents=True, exist_ok=True)
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRATION_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    CAREERS_CVS_DIR.mkdir(parents=True, exist_ok=True)

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
                    "preferred_consultation_date",
                    "preferred_consultation_time",
                    "message",
                    "ip_address",
                    "user_agent",
                ]
            )

    if not NEWSLETTER_FILE.exists():
        with NEWSLETTER_FILE.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["timestamp_utc", "email", "interest", "ip_address"])

    if not REGISTRATION_FILE.exists():
        with REGISTRATION_FILE.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "timestamp_utc",
                    "name",
                    "email",
                    "phone",
                    "highest_qualification",
                    "discipline",
                    "skill_level",
                    "past_training",
                    "preferred_course",
                    "preferred_days",
                    "preferred_start_date",
                    "preferred_time_slot",
                    "schedule_acknowledgement",
                    "pc_only_acknowledgement",
                    "commitment_acknowledgement",
                    "outside_hours_acknowledgement",
                    "has_pc",
                    "has_internet",
                    "additional_comments",
                    "receipt_original_name",
                    "receipt_relative_path",
                    "ip_address",
                    "user_agent",
                ]
            )

    if not CAREERS_FILE.exists():
        with CAREERS_FILE.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "timestamp_utc",
                    "name",
                    "email",
                    "phone",
                    "training_area",
                    "years_experience",
                    "availability",
                    "portfolio_url",
                    "message",
                    "cv_original_name",
                    "cv_relative_path",
                    "ip_address",
                    "user_agent",
                ]
            )

    db = connect_db()
    try:
        initialize_database_schema(db)
        migrate_database_schema(db)
        ensure_certificate_approvals_table(db)
        seed_database(db)
        db.commit()
    finally:
        db.close()


def append_csv_row(file_path: Path, row: list[str]) -> None:
    with file_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(row)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM users WHERE email = ?", (normalize_email(email),))


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))


def user_has_role(user: dict[str, Any] | None, allowed_roles: str | tuple[str, ...] | list[str] | set[str]) -> bool:
    if user is None:
        return False
    if isinstance(allowed_roles, str):
        return user["role"] == allowed_roles
    return user["role"] in allowed_roles


def login_user(user: dict[str, Any]) -> None:
    session["user_id"] = int(user["id"])
    session.pop("has_pending_feedback", None)


def logout_user() -> None:
    session.pop("user_id", None)
    session.pop("has_pending_feedback", None)


def is_user_approved(user: dict[str, Any] | None) -> bool:
    return bool(user and int(user.get("is_approved", 0)) == 1)


def is_user_suspended(user: dict[str, Any] | None) -> bool:
    return bool(user and int(user.get("is_suspended", 0)) == 1)


def must_change_password(user: dict[str, Any] | None) -> bool:
    return bool(user and int(user.get("must_change_password", 0)) == 1)


def password_reset_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return True
    return datetime.fromisoformat(expires_at) < datetime.now(tz=UTC)


@app.before_request
def show_maintenance_page() -> Any:
    if not app.config.get("MAINTENANCE_MODE"):
        return None

    if request.endpoint in {"static", "healthz", "maintenance"}:
        return None

    response = make_response(render_template("maintenance.html"), 503)
    response.headers["Retry-After"] = str(app.config["MAINTENANCE_RETRY_AFTER"])
    return response


@app.before_request
def load_current_user() -> None:
    user_id = session.get("user_id")
    g.user = get_user_by_id(int(user_id)) if user_id else None


@app.before_request
def validate_csrf_token() -> Any:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    submitted_token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if csrf_token_valid(submitted_token):
        return None

    app.logger.warning("Blocked CSRF validation failure for %s from %s", request.path, client_ip_address())
    return make_response("Invalid or missing CSRF token.", 400)


@app.before_request
def enforce_required_course_feedback() -> None:
    if (
        g.get("user") is None
        or g.user["role"] != "student"
        or not is_user_approved(g.user)
        or is_user_suspended(g.user)
        or request.endpoint in {
            "static",
            "student_dashboard",
            "submit_course_feedback",
            "logout",
        }
    ):
        return None

    # Check cached session state first
    has_pending = session.get("has_pending_feedback")
    if has_pending is False:
        return None

    if has_pending is True:
        flash("Please complete the course feedback form before continuing.", "error")
        return redirect(url_for("student_dashboard"))

    # Cache miss - query the database
    pending_feedback = get_pending_course_feedback(int(g.user["id"]))
    session["has_pending_feedback"] = (pending_feedback is not None)
    if pending_feedback is not None:
        flash("Please complete the course feedback form before continuing.", "error")
        return redirect(url_for("student_dashboard"))
    return None


def login_required(role: str | tuple[str, ...] | list[str] | set[str] | None = None):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args: Any, **kwargs: Any):
            if g.user is None:
                flash("Please sign in to access the Learning Hub.", "error")
                return redirect(url_for("login", next=request.path))
            if not is_user_approved(g.user):
                logout_user()
                flash("Your Learning Hub access is not active yet. Please use the login details sent by the admin after approval.", "error")
                return redirect(url_for("login"))
            if is_user_suspended(g.user):
                logout_user()
                flash("Account suspended. Contact the admin.", "error")
                return redirect(url_for("login"))
            if role and not user_has_role(g.user, role):
                flash("You do not have permission to access that page.", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped_view

    return decorator


def format_iso_label(value: str | None, *, include_time: bool = True) -> str:
    if not value:
        return "Not set"
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value)
    if include_time:
        return parsed.strftime("%b %d, %Y at %I:%M %p")
    return parsed.strftime("%b %d, %Y")


def normalize_due_at_input(due_at: str) -> str:
    due_value = due_at.strip().replace(" ", "T")
    if len(due_value) == 16:
        due_value = f"{due_value}:00+01:00"
    return due_value


def normalize_assignment_type(value: str) -> str:
    assignment_type = (value or "").strip().lower()
    if assignment_type in {"assignment", "quiz", "challenge", "project"}:
        return assignment_type
    return "assignment"


def normalize_class_post_kind(value: str) -> str:
    kind = (value or "").strip().lower()
    if kind in {"announcement", "resource", "assignment", "challenge", "quiz", "project"}:
        return kind
    return "announcement"


def infer_assignment_type(title: str, description: str = "") -> str:
    combined = f"{title} {description}".lower()
    if "quiz" in combined or "test" in combined:
        return "quiz"
    if any(keyword in combined for keyword in ("project", "capstone", "portfolio")):
        return "project"
    if any(keyword in combined for keyword in ("challenge", "capstone", "checkpoint", "brief")):
        return "challenge"
    return "assignment"


def assignment_type_label(value: str) -> str:
    mapping = {
        "assignment": "Class Assignment",
        "quiz": "Quiz",
        "challenge": "Class Challenge",
        "project": "Project",
    }
    return mapping.get(normalize_assignment_type(value), "Class Assignment")


def is_project_coursework(item: dict[str, Any]) -> bool:
    assignment_type = str(item.get("assignment_type") or "").strip().lower()
    combined = f"{item.get('title') or ''} {item.get('description') or ''}".lower()
    return assignment_type == "project" or any(
        keyword in combined
        for keyword in ("project", "capstone", "portfolio")
    )


def student_passed_project(classroom_id: int, student_id: int) -> bool:
    rows = fetch_all(
        """
        SELECT
            a.assignment_type,
            a.title,
            a.description,
            s.status,
            s.grade
        FROM assignments a
        JOIN submissions s ON s.assignment_id = a.id
        WHERE a.classroom_id = ? AND s.student_id = ?
        """,
        (classroom_id, student_id),
    )
    for row in rows:
        if not is_project_coursework(row):
            continue
        status = (row.get("status") or "").strip().lower()
        grade = (row.get("grade") or "").strip().lower()
        if status in {"approved", "passed", "pass"} or grade in {"pass", "passed"}:
            return True
    return False



def student_coursework_completion(classroom_id: int, student_id: int) -> dict[str, Any]:
    rows = fetch_all(
        """
        SELECT
            a.assignment_type,
            a.title,
            a.description,
            s.id AS submission_id
        FROM assignments a
        LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_id = ?
        WHERE a.classroom_id = ?
        """,
        (student_id, classroom_id),
    )
    assignment_total = 0
    assignment_submitted = 0
    project_total = 0
    project_submitted = 0
    for row in rows:
        submitted = row.get("submission_id") is not None
        if is_project_coursework(row):
            project_total += 1
            if submitted:
                project_submitted += 1
        else:
            assignment_total += 1
            if submitted:
                assignment_submitted += 1
    coursework_total = assignment_total + project_total
    coursework_submitted = assignment_submitted + project_submitted
    return {
        "assignment_total": assignment_total,
        "assignment_submitted": assignment_submitted,
        "project_total": project_total,
        "project_submitted": project_submitted,
        "coursework_total": coursework_total,
        "coursework_submitted": coursework_submitted,
        "assignments_complete": assignment_submitted >= assignment_total,
        "projects_submitted": project_total > 0 and project_submitted >= project_total,
        "all_coursework_submitted": coursework_total > 0 and coursework_submitted >= coursework_total and project_total > 0,
    }


def ensure_certificate_approvals_table(db: Any) -> None:
    try:
        if DATABASE_ENGINE == "postgresql":
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS certificate_approvals (
                    id SERIAL PRIMARY KEY,
                    classroom_id INTEGER NOT NULL,
                    student_id INTEGER NOT NULL,
                    approved_by INTEGER NOT NULL,
                    approved_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revoked_by INTEGER,
                    CONSTRAINT unique_classroom_student UNIQUE(classroom_id, student_id)
                )
                """
            )
        else:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS certificate_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    classroom_id INTEGER NOT NULL,
                    student_id INTEGER NOT NULL,
                    approved_by INTEGER NOT NULL,
                    approved_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revoked_by INTEGER,
                    UNIQUE(classroom_id, student_id)
                )
                """
            )
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        app.logger.warning("Could not create certificate_approvals table: %s", exc)


def certificate_approval_record(classroom_id: int, student_id: int) -> dict[str, Any] | None:
    db = get_db()
    try:
        return fetch_one(
            """
            SELECT ca.*, approver.name AS approved_by_name
            FROM certificate_approvals ca
            JOIN users approver ON approver.id = ca.approved_by
            WHERE ca.classroom_id = ? AND ca.student_id = ? AND ca.revoked_at IS NULL
            """,
            (classroom_id, student_id),
        )
    except Exception as exc:
        app.logger.warning("Querying certificate_approvals failed (%s), rolling back and creating table...", exc)
        try:
            db.rollback()
        except Exception:
            pass
        ensure_certificate_approvals_table(db)
        try:
            return fetch_one(
                """
                SELECT ca.*, approver.name AS approved_by_name
                FROM certificate_approvals ca
                JOIN users approver ON approver.id = ca.approved_by
                WHERE ca.classroom_id = ? AND ca.student_id = ? AND ca.revoked_at IS NULL
                """,
                (classroom_id, student_id),
            )
        except Exception as retry_exc:
            app.logger.warning("Retry querying certificate_approvals failed (%s), rolling back...", retry_exc)
            try:
                db.rollback()
            except Exception:
                pass
            return None


def certificate_eligibility_status(classroom_id: int, student_id: int, attendance_progress: int) -> dict[str, Any]:
    coursework = student_coursework_completion(classroom_id, student_id)
    project_passed = student_passed_project(classroom_id, student_id)
    approval = certificate_approval_record(classroom_id, student_id)
    return {
        **coursework,
        "attendance_complete": int(attendance_progress) >= 100,
        "project_passed": project_passed,
        "admin_approved": approval is not None,
        "approval": approval,
        "certificate_unlocked": int(attendance_progress) >= 100
        and coursework["all_coursework_submitted"]
        and project_passed
        and approval is not None,
    }

def submission_timing_status(due_at: str, submitted_at: str | None) -> str:
    if not submitted_at:
        return "Not submitted"
    due_date = datetime.fromisoformat(due_at)
    submitted_date = datetime.fromisoformat(submitted_at)
    if submitted_date > due_date:
        return "Late submission"
    return "Submitted on time"


def normalize_attendance_status(value: str) -> str:
    status = (value or "").strip().lower()
    if status in {"present", "late", "absent"}:
        return status
    return "present"


def course_duration_weeks(course_duration: str | None) -> int | None:
    duration_text = (course_duration or "").strip().lower()
    match = re.search(r"(\d+)", duration_text)
    if not match:
        return None
    return int(match.group(1))


def attendance_target_from_duration(course_duration: str | None, fallback: int = 8) -> int:
    weeks = course_duration_weeks(course_duration)
    if weeks is None:
        return max(int(fallback or 0), 0)
    return max(weeks * 2, 0)


def attendance_target_for_classroom(classroom_id: int) -> int:
    row = fetch_one("SELECT course_duration, attendance_target FROM classrooms WHERE id = ?", (classroom_id,))
    if not row:
        return 8
    return attendance_target_from_duration(row["course_duration"], int(row["attendance_target"] or 8))


def classroom_timeline_details(classroom: dict[str, Any]) -> dict[str, Any]:
    start_raw = (classroom.get("course_start_date") or classroom.get("created_at") or timestamp_now())[:10]
    try:
        start_date = datetime.fromisoformat(start_raw).date()
    except ValueError:
        start_date = datetime.now(tz=UTC).date()

    weeks = course_duration_weeks(classroom.get("course_duration"))
    end_date = start_date + timedelta(weeks=weeks or 0)
    expected_classes = attendance_target_from_duration(
        classroom.get("course_duration"),
        int(classroom.get("attendance_target") or 8),
    )
    return {
        "course_start_date": start_date.isoformat(),
        "course_start_label": start_date.strftime("%b %d, %Y"),
        "course_end_label": end_date.strftime("%b %d, %Y") if weeks else "Not set",
        "expected_classes": expected_classes,
    }


def attendance_progress_for_student(classroom_id: int, student_id: int, attendance_target: int | None = None) -> dict[str, int]:
    target = attendance_target if attendance_target is not None else attendance_target_for_classroom(classroom_id)
    row = fetch_one(
        """
        SELECT
            COUNT(DISTINCT CASE WHEN r.status IN ('present', 'late') THEN s.id END) AS attended_sessions
        FROM attendance_sessions s
        LEFT JOIN attendance_records r ON r.session_id = s.id AND r.student_id = ?
        WHERE s.classroom_id = ?
        """,
        (student_id, classroom_id),
    )
    attended_sessions = int(row["attended_sessions"] or 0) if row else 0
    total_sessions = max(int(target or 0), 0)
    progress = round((attended_sessions / total_sessions) * 100) if total_sessions else 0
    return {
        "attendance_total": total_sessions,
        "attendance_attended": attended_sessions,
        "attendance_progress": min(progress, 100),
    }


def attendance_progress_for_classroom(classroom_id: int, attendance_target: int | None = None) -> dict[str, int]:
    target = attendance_target if attendance_target is not None else attendance_target_for_classroom(classroom_id)
    row = fetch_one(
        """
        SELECT
            COUNT(DISTINCT e.user_id) AS learner_count,
            COUNT(DISTINCT CASE WHEN r.status IN ('present', 'late') THEN CAST(r.student_id AS TEXT) || '-' || CAST(r.session_id AS TEXT) END) AS attended_sessions
        FROM enrollments e
        LEFT JOIN attendance_sessions s ON s.classroom_id = e.classroom_id
        LEFT JOIN attendance_records r ON r.session_id = s.id AND r.student_id = e.user_id
        WHERE e.classroom_id = ?
        """,
        (classroom_id,),
    )
    total_sessions = max(int(target or 0), 0)
    learner_count = int(row["learner_count"] or 0) if row else 0
    attended_sessions = int(row["attended_sessions"] or 0) if row else 0
    possible_attendance = total_sessions * learner_count
    progress = round((attended_sessions / possible_attendance) * 100) if possible_attendance else 0
    return {
        "attendance_total": total_sessions,
        "attendance_attended": attended_sessions,
        "attendance_possible": possible_attendance,
        "attendance_progress": min(progress, 100),
    }


def rating_percentage(score_total: int) -> int:
    return round((score_total / FEEDBACK_MAX_SCORE) * 100)


def parse_feedback_rating(field_name: str) -> int | None:
    try:
        rating = int(request.form.get(field_name, ""))
    except ValueError:
        return None
    if 1 <= rating <= 5:
        return rating
    return None


def get_pending_course_feedback(user_id: int) -> dict[str, Any] | None:
    classrooms = fetch_all(
        """
        SELECT
            c.id,
            c.slug,
            c.title,
            c.code,
            c.cohort,
            mentor.name AS mentor_name,
            COUNT(r.id) AS attendance_count
        FROM enrollments e
        JOIN classrooms c ON c.id = e.classroom_id
        JOIN users mentor ON mentor.id = c.mentor_id
        LEFT JOIN attendance_sessions s ON s.classroom_id = c.id
        LEFT JOIN attendance_records r ON r.session_id = s.id
            AND r.student_id = e.user_id
            AND r.status IN ('present', 'late')
        WHERE e.user_id = ?
        GROUP BY c.id, mentor.name
        HAVING COUNT(r.id) >= ?
        ORDER BY c.title ASC
        """,
        (user_id, min(FEEDBACK_MILESTONES)),
    )
    for classroom in classrooms:
        attendance_count = int(classroom["attendance_count"] or 0)
        for milestone in FEEDBACK_MILESTONES:
            if attendance_count < milestone:
                continue
            existing_feedback = fetch_one(
                """
                SELECT id
                FROM course_feedback
                WHERE classroom_id = ? AND student_id = ? AND milestone = ?
                """,
                (int(classroom["id"]), user_id, milestone),
            )
            if existing_feedback is None:
                classroom["milestone"] = milestone
                classroom["attendance_count"] = attendance_count
                return classroom
    return None


def get_lms_overview_stats() -> list[dict[str, str]]:
    db = get_db()
    active_classrooms = db.execute("SELECT COUNT(*) FROM classrooms").fetchone()[0]
    weekly_live_sessions = db.execute("SELECT COALESCE(SUM(live_sessions), 0) FROM classrooms").fetchone()[0]
    submission_row = db.execute(
        """
        SELECT
            COUNT(s.id) AS submitted_count,
            COUNT(DISTINCT CAST(e.user_id AS TEXT) || '-' || CAST(a.id AS TEXT)) AS expected_count
        FROM classrooms c
        LEFT JOIN enrollments e ON e.classroom_id = c.id
        LEFT JOIN assignments a ON a.classroom_id = c.id
        LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_id = e.user_id
        """
    ).fetchone()
    expected_count = int(submission_row["expected_count"] or 0)
    submitted_count = int(submission_row["submitted_count"] or 0)
    completion_rate = int((submitted_count / expected_count) * 100) if expected_count else 0
    return [
        {"label": "Active classrooms", "value": str(active_classrooms)},
        {"label": "Weekly live sessions", "value": str(weekly_live_sessions)},
        {"label": "Assignment completion", "value": f"{completion_rate}%"},
        {"label": "Mentor response target", "value": "< 12 hrs"},
    ]


def get_public_classrooms() -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT
            c.*,
            u.name AS mentor_name,
            COUNT(DISTINCT e.user_id) AS member_count,
            COUNT(DISTINCT a.id) AS assignment_count
        FROM classrooms c
        JOIN users u ON u.id = c.mentor_id
        LEFT JOIN enrollments e ON e.classroom_id = c.id
        LEFT JOIN assignments a ON a.classroom_id = c.id
        GROUP BY c.id, u.name
        ORDER BY c.title ASC
        """
    )
    for row in rows:
        row["members"] = row.pop("member_count")
        row["assignments_due"] = row.pop("assignment_count")
        row["mentor"] = row.pop("mentor_name")
    return rows


def rewrite_meeting_links_if_mock(meeting: dict[str, Any]) -> None:
    meeting_key = str(meeting.get("meeting_key") or "")
    if meeting_key.startswith("mock-") or "key=mock-" in str(meeting.get("start_link") or "") or "key=mock-" in str(meeting.get("join_link") or ""):
        try:
            base_url = request.url_root.rstrip("/")
        except RuntimeError:
            base_url = f"https://{MAIL_DOMAIN}"
        meeting["start_link"] = f"{base_url}/lms/meeting/mock/{meeting_key}?role=host"
        meeting["join_link"] = f"{base_url}/lms/meeting/mock/{meeting_key}?role=student"


def get_upcoming_meetings_for_user(user_id: int, role: str) -> list[dict[str, Any]]:
    db = get_db()
    if role == "student":
        rows = db.execute(
            """
            SELECT s.*, c.title AS classroom_title, c.slug AS classroom_slug
            FROM live_sessions s
            JOIN classrooms c ON c.id = s.classroom_id
            JOIN enrollments e ON e.classroom_id = c.id
            WHERE e.user_id = ?
            ORDER BY s.start_time ASC
            """,
            (user_id,)
        ).fetchall()
    elif role == "mentor":
        rows = db.execute(
            """
            SELECT s.*, c.title AS classroom_title, c.slug AS classroom_slug
            FROM live_sessions s
            JOIN classrooms c ON c.id = s.classroom_id
            WHERE c.mentor_id = ?
            ORDER BY s.start_time ASC
            """,
            (user_id,)
        ).fetchall()
    else:
        return []

    meetings = [dict(r) for r in rows]
    upcoming_meetings = []
    now_val = datetime.now(tz=UTC)
    for meeting in meetings:
        rewrite_meeting_links_if_mock(meeting)
        try:
            start_dt = datetime.fromisoformat(meeting["start_time"])
            duration_delta = timedelta(minutes=int(meeting["duration"]))
            end_dt = start_dt + duration_delta

            session_is_completed = now_val > end_dt
            session_is_live = (start_dt - timedelta(minutes=15)) <= now_val <= end_dt
        except Exception:
            session_is_completed = False
            session_is_live = False

        if not session_is_completed:
            meeting["is_live"] = session_is_live
            meeting["start_time_formatted"] = format_iso_label(meeting["start_time"])
            upcoming_meetings.append(meeting)

    return upcoming_meetings


def get_student_dashboard_data(user_id: int) -> dict[str, Any]:
    classrooms = fetch_all(
        """
        SELECT
            c.id,
            c.slug,
            c.title,
            c.code,
            c.cohort,
            c.schedule,
            c.course_duration,
            c.theme,
            c.description,
            c.completion,
            c.attendance_target,
            u.name AS mentor_name,
            COUNT(DISTINCT a.id) AS assignment_total,
            COUNT(DISTINCT s.id) AS submitted_total,
            COUNT(DISTINCT m.id) AS material_total
        FROM enrollments e
        JOIN classrooms c ON c.id = e.classroom_id
        JOIN users u ON u.id = c.mentor_id
        LEFT JOIN assignments a ON a.classroom_id = c.id
        LEFT JOIN materials m ON m.classroom_id = c.id
        LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_id = e.user_id
        WHERE e.user_id = ?
        GROUP BY c.id, u.name
        ORDER BY c.title ASC
        """,
        (user_id,),
    )
    assignments = fetch_all(
        """
        SELECT
            a.id,
            a.assignment_type,
            a.title,
            a.description,
            a.due_at,
            c.slug,
            c.title AS classroom_title,
            COALESCE(s.status, 'not submitted') AS submission_status
        FROM enrollments e
        JOIN classrooms c ON c.id = e.classroom_id
        JOIN assignments a ON a.classroom_id = c.id
        LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_id = e.user_id
        WHERE e.user_id = ?
        ORDER BY a.due_at ASC
        LIMIT 8
        """,
        (user_id,),
    )
    announcements = fetch_all(
        """
        SELECT
            an.title,
            an.kind,
            an.posted_at,
            c.slug,
            c.title AS classroom_title
        FROM enrollments e
        JOIN classrooms c ON c.id = e.classroom_id
        JOIN announcements an ON an.classroom_id = c.id
        WHERE e.user_id = ?
        ORDER BY an.posted_at DESC
        LIMIT 6
        """,
        (user_id,),
    )
    for announcement in announcements:
        announcement["posted_label"] = format_iso_label(announcement["posted_at"])
    for classroom in classrooms:
        classroom.update(
            attendance_progress_for_student(
                int(classroom["id"]),
                user_id,
                attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
            )
        )
        classroom["progress"] = classroom["attendance_progress"]
        classroom["progress_label"] = f"{classroom['attendance_attended']}/{classroom['attendance_total']} sessions attended"
        classroom["project_unlocked"] = int(classroom["progress"]) >= PROJECT_UNLOCK_PROGRESS
        classroom["project_unlock_remaining"] = max(PROJECT_UNLOCK_PROGRESS - int(classroom["progress"]), 0)
        certificate_status = certificate_eligibility_status(int(classroom["id"]), user_id, int(classroom["progress"]))
        classroom.update(certificate_status)
    project_unlocked_slugs = {classroom["slug"] for classroom in classrooms if classroom["project_unlocked"]}
    visible_assignments = []
    projects = []
    for assignment in assignments:
        assignment["due_label"] = format_iso_label(assignment["due_at"])
        if is_project_coursework(assignment):
            assignment["project_unlocked"] = assignment["slug"] in project_unlocked_slugs
            projects.append(assignment)
            continue
        visible_assignments.append(assignment)

    try:
        suggestions = fetch_all(
            """
            SELECT
                ps.*,
                c.slug,
                c.title AS classroom_title
            FROM project_suggestions ps
            JOIN classrooms c ON c.id = ps.classroom_id
            WHERE ps.student_id = ?
            ORDER BY ps.submitted_at DESC
            """,
            (user_id,),
        )
    except Exception as exc:
        app.logger.warning("Fetching project_suggestions failed (%s), rolling back...", exc)
        try:
            get_db().rollback()
        except Exception:
            pass
        suggestions = []
    for suggestion in suggestions:
        suggestion["submitted_label"] = format_iso_label(suggestion["submitted_at"])

    completed = sum(int(item["submitted_total"]) for item in classrooms)
    total = sum(int(item["assignment_total"]) for item in classrooms)
    sessions_remaining = sum(
        max(int(item["attendance_total"]) - int(item["attendance_attended"]), 0)
        for item in classrooms
    )
    average_progress = round(sum(int(item["progress"]) for item in classrooms) / len(classrooms)) if classrooms else 0
    certificate_classrooms = [
        classroom
        for classroom in classrooms
        if classroom["certificate_unlocked"]
    ]
    meetings = get_upcoming_meetings_for_user(user_id, "student")
    return {
        "classrooms": classrooms,
        "certificate_classrooms": certificate_classrooms,
        "assignments": visible_assignments,
        "projects": projects,
        "project_suggestions": suggestions,
        "announcements": announcements,
        "meetings": meetings,
        "stats": [
            {"label": "Enrolled classrooms", "value": str(len(classrooms))},
            {"label": "Average progress", "value": f"{average_progress}%"},
            {"label": "Sessions remaining", "value": str(sessions_remaining)},
            {"label": "Assignments submitted", "value": str(completed)},
            {"label": "Assignments available", "value": str(total)},
        ],
    }


def get_mentor_dashboard_data(user_id: int) -> dict[str, Any]:
    classrooms = fetch_all(
        """
        SELECT
            c.id,
            c.slug,
            c.title,
            c.code,
            c.cohort,
            c.schedule,
            c.course_duration,
            c.theme,
            c.description,
            c.completion,
            c.attendance_target,
            COUNT(DISTINCT e.user_id) AS student_count,
            COUNT(DISTINCT a.id) AS assignment_count,
            COUNT(DISTINCT s.id) AS submission_count,
            COUNT(DISTINCT m.id) AS material_count
        FROM classrooms c
        LEFT JOIN enrollments e ON e.classroom_id = c.id
        LEFT JOIN assignments a ON a.classroom_id = c.id
        LEFT JOIN materials m ON m.classroom_id = c.id
        LEFT JOIN submissions s ON s.assignment_id = a.id
        WHERE c.mentor_id = ?
        GROUP BY c.id
        ORDER BY c.title ASC
        """,
        (user_id,),
    )
    submissions = fetch_all(
        """
        SELECT
            s.id,
            s.status,
            s.submitted_at,
            s.grade,
            s.feedback,
            a.title AS assignment_title,
            c.slug,
            c.title AS classroom_title,
            u.name AS student_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN classrooms c ON c.id = a.classroom_id
        JOIN users u ON u.id = s.student_id
        WHERE c.mentor_id = ?
        ORDER BY s.submitted_at DESC
        LIMIT 10
        """,
        (user_id,),
    )
    for submission in submissions:
        submission["submitted_label"] = format_iso_label(submission["submitted_at"])
    for classroom in classrooms:
        classroom.update(
            attendance_progress_for_classroom(
                int(classroom["id"]),
                attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
            )
        )
        classroom["progress"] = classroom["attendance_progress"]
        classroom["progress_label"] = f"{classroom['attendance_attended']}/{classroom['attendance_possible']} possible attendances"

    total_students = sum(int(item["student_count"]) for item in classrooms)
    average_progress = round(sum(int(item["progress"]) for item in classrooms) / len(classrooms)) if classrooms else 0
    meetings = get_upcoming_meetings_for_user(user_id, "mentor")
    return {
        "classrooms": classrooms,
        "submissions": submissions,
        "meetings": meetings,
        "stats": [
            {"label": "Managed classrooms", "value": str(len(classrooms))},
            {"label": "Enrolled learners", "value": str(total_students)},
            {"label": "Average progress", "value": f"{average_progress}%"},
            {"label": "Submissions received", "value": str(sum(int(item["submission_count"]) for item in classrooms))},
        ],
    }


def get_admin_dashboard_data() -> dict[str, Any]:
    stats_row = get_db().execute(
        """
        SELECT
            SUM(CASE WHEN role = 'student' THEN 1 ELSE 0 END) AS student_total,
            SUM(CASE WHEN role = 'mentor' THEN 1 ELSE 0 END) AS mentor_total,
            SUM(CASE WHEN role = 'admin' THEN 1 ELSE 0 END) AS admin_total,
            SUM(CASE WHEN is_approved = 1 THEN 1 ELSE 0 END) AS approved_total
        FROM users
        """
    ).fetchone()
    classroom_total = get_db().execute("SELECT COUNT(*) FROM classrooms").fetchone()[0]
    material_total = get_db().execute("SELECT COUNT(*) FROM materials").fetchone()[0]
    submission_total = get_db().execute("SELECT COUNT(*) FROM submissions").fetchone()[0]

    users = fetch_all(
        """
        SELECT
            u.id,
            u.name,
            u.email,
            u.role,
            u.is_approved,
            u.is_suspended,
            u.suspended_at,
            u.approved_at,
            u.created_at,
            COUNT(DISTINCT e.classroom_id) AS classroom_count,
            COUNT(DISTINCT c.id) AS assigned_classroom_count
        FROM users u
        LEFT JOIN enrollments e ON e.user_id = u.id
        LEFT JOIN classrooms c ON c.mentor_id = u.id
        GROUP BY u.id
        ORDER BY
            CASE u.role WHEN 'admin' THEN 0 WHEN 'mentor' THEN 1 ELSE 2 END,
            u.name ASC
        """
    )
    for user in users:
        user["created_label"] = format_iso_label(user["created_at"])
        user["approved_label"] = format_iso_label(user["approved_at"]) if user.get("approved_at") else "Pending"
        user["suspended_label"] = format_iso_label(user["suspended_at"]) if user.get("suspended_at") else ""

    classrooms = fetch_all(
        """
        SELECT
            c.id,
            c.slug,
            c.title,
            c.code,
            c.cohort,
            c.schedule,
            c.course_duration,
            c.course_start_date,
            c.description,
            c.completion,
            c.attendance_target,
            c.live_sessions,
            c.theme,
            c.hero_metric,
            c.mentor_id,
            u.name AS mentor_name,
            COUNT(DISTINCT e.user_id) AS learner_count,
            COUNT(DISTINCT a.id) AS assignment_count,
            COUNT(DISTINCT m.id) AS material_count,
            COUNT(DISTINCT s.id) AS submission_count
        FROM classrooms c
        JOIN users u ON u.id = c.mentor_id
        LEFT JOIN enrollments e ON e.classroom_id = c.id
        LEFT JOIN assignments a ON a.classroom_id = c.id
        LEFT JOIN materials m ON m.classroom_id = c.id
        LEFT JOIN submissions s ON s.assignment_id = a.id
        GROUP BY c.id, u.name
        ORDER BY c.title ASC
        """
    )
    classroom_learners = fetch_all(
        """
        SELECT
            e.classroom_id,
            u.id,
            u.name,
            u.email
        FROM enrollments e
        JOIN users u ON u.id = e.user_id
        WHERE u.role = 'student'
        ORDER BY u.name ASC
        """
    )
    learners_by_classroom: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for learner in classroom_learners:
        learners_by_classroom[int(learner["classroom_id"])].append(learner)
    for classroom in classrooms:
        classroom.update(classroom_timeline_details(classroom))
        classroom["learners"] = learners_by_classroom[int(classroom["id"])]
        classroom.update(
            attendance_progress_for_classroom(
                int(classroom["id"]),
                attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
            )
        )
        classroom["progress"] = classroom["attendance_progress"]
        classroom["progress_label"] = f"{classroom['attendance_attended']}/{classroom['attendance_possible']} possible attendances"

    recent_submissions = fetch_all(
        """
        SELECT
            s.id,
            s.status,
            s.submitted_at,
            s.grade,
            a.title AS assignment_title,
            c.slug,
            c.title AS classroom_title,
            learner.name AS student_name,
            mentor.name AS mentor_name
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN classrooms c ON c.id = a.classroom_id
        JOIN users learner ON learner.id = s.student_id
        JOIN users mentor ON mentor.id = c.mentor_id
        ORDER BY s.submitted_at DESC
        LIMIT 12
        """
    )
    for submission in recent_submissions:
        submission["submitted_label"] = format_iso_label(submission["submitted_at"])

    feedback_summary = fetch_all(
        """
        SELECT
            c.id AS classroom_id,
            c.title AS classroom_title,
            c.code AS classroom_code,
            mentor.name AS mentor_name,
            COUNT(f.id) AS response_count,
            ROUND(AVG(f.score_percentage)) AS average_score,
            ROUND(AVG(f.course_experience) * 20) AS course_experience_score,
            ROUND(AVG(f.course_content) * 20) AS course_content_score,
            ROUND(AVG(f.mentor_methodology) * 20) AS mentor_methodology_score,
            ROUND(AVG(f.mentor_experience) * 20) AS mentor_experience_score,
            ROUND(AVG(f.mentor_knowledge) * 20) AS mentor_knowledge_score,
            ROUND(AVG(f.lms_ease) * 20) AS lms_ease_score
        FROM course_feedback f
        JOIN classrooms c ON c.id = f.classroom_id
        JOIN users mentor ON mentor.id = c.mentor_id
        GROUP BY c.id, mentor.name
        ORDER BY average_score ASC, c.title ASC
        """
    )
    for item in feedback_summary:
        item["average_score"] = int(item["average_score"] or 0)
        item["course_experience_score"] = int(item["course_experience_score"] or 0)
        item["course_content_score"] = int(item["course_content_score"] or 0)
        item["mentor_methodology_score"] = int(item["mentor_methodology_score"] or 0)
        item["mentor_experience_score"] = int(item["mentor_experience_score"] or 0)
        item["mentor_knowledge_score"] = int(item["mentor_knowledge_score"] or 0)
        item["lms_ease_score"] = int(item["lms_ease_score"] or 0)

    recent_feedback = fetch_all(
        """
        SELECT
            f.*,
            c.title AS classroom_title,
            c.code AS classroom_code,
            learner.name AS student_name,
            mentor.name AS mentor_name
        FROM course_feedback f
        JOIN classrooms c ON c.id = f.classroom_id
        JOIN users learner ON learner.id = f.student_id
        JOIN users mentor ON mentor.id = c.mentor_id
        ORDER BY f.submitted_at DESC
        LIMIT 12
        """
    )
    for feedback in recent_feedback:
        feedback["submitted_label"] = format_iso_label(feedback["submitted_at"])

    feedback_average_row = get_db().execute("SELECT ROUND(AVG(score_percentage)) FROM course_feedback").fetchone()
    feedback_average = int(feedback_average_row[0] or 0) if feedback_average_row else 0

    mentors = fetch_all("SELECT id, name, email FROM users WHERE role = 'mentor' ORDER BY name ASC")
    students = fetch_all("SELECT id, name, email FROM users WHERE role = 'student' ORDER BY name ASC")
    role_counts = {
        "all": int(stats_row["student_total"] or 0) + int(stats_row["mentor_total"] or 0) + int(stats_row["admin_total"] or 0),
        "student": int(stats_row["student_total"] or 0),
        "mentor": int(stats_row["mentor_total"] or 0),
        "admin": int(stats_row["admin_total"] or 0),
    }

    return {
        "stats": [
            {"label": "Learners", "value": str(int(stats_row["student_total"] or 0))},
            {"label": "Mentors", "value": str(int(stats_row["mentor_total"] or 0))},
            {"label": "Classrooms", "value": str(classroom_total)},
            {"label": "Submissions", "value": str(submission_total)},
            {"label": "Resources", "value": str(material_total)},
            {"label": "Approved users", "value": str(int(stats_row["approved_total"] or 0))},
            {"label": "Feedback average", "value": f"{feedback_average}%"},
        ],
        "role_counts": role_counts,
        "users": users,
        "classrooms": classrooms,
        "recent_submissions": recent_submissions,
        "feedback_summary": feedback_summary,
        "recent_feedback": recent_feedback,
        "mentors": mentors,
        "students": students,
    }


def get_classroom_for_user(slug: str, user: dict[str, Any]) -> dict[str, Any] | None:
    classroom = fetch_one(
        """
        SELECT
            c.*,
            u.name AS mentor_name,
            u.email AS mentor_email,
            COUNT(DISTINCT e.user_id) AS member_count
        FROM classrooms c
        JOIN users u ON u.id = c.mentor_id
        LEFT JOIN enrollments e ON e.classroom_id = c.id
        WHERE c.slug = ?
        GROUP BY c.id, u.name, u.email
        """,
        (slug,),
    )
    if classroom is None:
        return None

    if user["role"] == "mentor":
        if int(classroom["mentor_id"]) != int(user["id"]):
            return None
    elif user["role"] == "student":
        enrolled = fetch_one(
            "SELECT id FROM enrollments WHERE classroom_id = ? AND user_id = ?",
            (int(classroom["id"]), int(user["id"])),
        )
        if enrolled is None:
            return None

    announcements = fetch_all(
        """
        SELECT id, kind, title, body, posted_at
        FROM announcements
        WHERE classroom_id = ?
        ORDER BY posted_at DESC
        """,
        (int(classroom["id"]),),
    )
    for announcement in announcements:
        announcement["posted_label"] = format_iso_label(announcement["posted_at"])

    modules = fetch_all(
        """
        SELECT id, title, status, summary, position
        FROM modules
        WHERE classroom_id = ?
        ORDER BY position ASC
        """,
        (int(classroom["id"]),),
    )
    module_ids = [int(module["id"]) for module in modules]
    items_by_module: dict[int, list[str]] = defaultdict(list)
    if module_ids:
        placeholders = ", ".join("?" for _ in module_ids)
        item_rows = fetch_all(
            f"""
            SELECT module_id, content
            FROM module_items
            WHERE module_id IN ({placeholders})
            ORDER BY position ASC
            """,
            tuple(module_ids),
        )
        for item in item_rows:
            items_by_module[int(item["module_id"])].append(item["content"])
    for module in modules:
        module["items"] = items_by_module.get(int(module["id"]), [])

    materials = fetch_all(
        """
        SELECT
            m.id,
            m.title,
            m.description,
            m.material_url,
            m.original_name,
            m.uploaded_at,
            u.name AS uploader_name
        FROM materials m
        JOIN users u ON u.id = m.uploader_id
        WHERE m.classroom_id = ?
        ORDER BY m.uploaded_at DESC
        """,
        (int(classroom["id"]),),
    )
    for material in materials:
        material["uploaded_label"] = format_iso_label(material["uploaded_at"])

    if user["role"] == "student":
        classroom.update(
            attendance_progress_for_student(
                int(classroom["id"]),
                int(user["id"]),
                attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
            )
        )
    else:
        classroom.update(
            attendance_progress_for_classroom(
                int(classroom["id"]),
                attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
            )
        )
    classroom["progress"] = classroom["attendance_progress"]
    if user["role"] == "student":
        classroom["progress_label"] = f"{classroom['attendance_attended']}/{classroom['attendance_total']} sessions attended"
    else:
        classroom["progress_label"] = f"{classroom['attendance_attended']}/{classroom['attendance_possible']} possible attendances"
    classroom.update(classroom_timeline_details(classroom))

    if user["role"] == "student":
        assignments = fetch_all(
            """
            SELECT
                a.id,
                a.assignment_type,
                a.title,
                a.description,
                a.due_at,
                s.id AS submission_id,
                s.content AS submission_content,
                s.status AS submission_status,
                s.submitted_at,
                s.grade,
                s.feedback,
                s.original_name AS submission_file_name,
                COALESCE(s.resubmission_allowed, 0) AS resubmission_allowed
            FROM assignments a
            LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_id = ?
            WHERE a.classroom_id = ?
            ORDER BY a.due_at ASC
            """,
            (int(user["id"]), int(classroom["id"])),
        )
        classroom["project_unlocked"] = int(classroom["progress"]) >= PROJECT_UNLOCK_PROGRESS
        classroom["project_unlock_remaining"] = max(PROJECT_UNLOCK_PROGRESS - int(classroom["progress"]), 0)
        classroom.update(certificate_eligibility_status(int(classroom["id"]), int(user["id"]), int(classroom["progress"])))
        for assignment in assignments:
            assignment["due_label"] = format_iso_label(assignment["due_at"])
            assignment["due_input_value"] = assignment["due_at"][:16] if assignment["due_at"] else ""
            assignment["assignment_type_label"] = assignment_type_label(assignment["assignment_type"])
            assignment["is_project"] = is_project_coursework(assignment)
            if assignment["submitted_at"]:
                assignment["submitted_label"] = format_iso_label(assignment["submitted_at"])
            assignment["submission_locked"] = submission_locked(assignment)
            assignment["timing_status"] = submission_timing_status(assignment["due_at"], assignment["submitted_at"])
            assignment["score_display"] = assignment["grade"] or "Pending"
        project_assignments = [assignment for assignment in assignments if assignment["is_project"]]
        assignments = [assignment for assignment in assignments if not assignment["is_project"]]
        score_tabulation = assignments + project_assignments
    else:
        classroom["project_unlocked"] = True
        classroom["project_unlock_remaining"] = 0
        classroom["project_passed"] = False
        classroom["certificate_unlocked"] = False
        assignments = fetch_all(
            """
            SELECT
                a.id,
                a.assignment_type,
                a.title,
                a.description,
                a.due_at,
                COUNT(DISTINCT s.id) AS submission_count
            FROM assignments a
            LEFT JOIN submissions s ON s.assignment_id = a.id
            WHERE a.classroom_id = ?
            GROUP BY a.id
            ORDER BY a.due_at ASC
            """,
            (int(classroom["id"]),),
        )
        for assignment in assignments:
            assignment["due_label"] = format_iso_label(assignment["due_at"])
            assignment["due_input_value"] = assignment["due_at"][:16] if assignment["due_at"] else ""
            assignment["assignment_type_label"] = assignment_type_label(assignment["assignment_type"])
            assignment["is_project"] = is_project_coursework(assignment)
        project_assignments = [assignment for assignment in assignments if assignment["is_project"]]
        assignments = [assignment for assignment in assignments if not assignment["is_project"]]

        mentor_submissions = fetch_all(
            """
            SELECT
                s.id,
                s.content,
                s.status,
                s.submitted_at,
                s.grade,
                s.feedback,
                COALESCE(s.resubmission_allowed, 0) AS resubmission_allowed,
                a.assignment_type,
                a.due_at,
                s.original_name AS submission_file_name,
                a.title AS assignment_title,
                u.name AS student_name
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            JOIN users u ON u.id = s.student_id
            WHERE a.classroom_id = ?
            ORDER BY s.submitted_at DESC
            """,
            (int(classroom["id"]),),
        )
        for submission in mentor_submissions:
            submission["submitted_label"] = format_iso_label(submission["submitted_at"])
            submission["assignment_type_label"] = assignment_type_label(submission["assignment_type"])
            submission["due_label"] = format_iso_label(submission["due_at"])
            submission["timing_status"] = submission_timing_status(submission["due_at"], submission["submitted_at"])

        score_tabulation = fetch_all(
            """
            SELECT
                u.name AS student_name,
                a.title AS assignment_title,
                a.assignment_type,
                a.due_at,
                s.submitted_at,
                s.grade,
                COALESCE(s.status, 'not submitted') AS submission_status
            FROM enrollments e
            JOIN users u ON u.id = e.user_id
            JOIN assignments a ON a.classroom_id = e.classroom_id
            LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_id = e.user_id
            WHERE e.classroom_id = ?
            ORDER BY u.name ASC, a.due_at ASC, a.title ASC
            """,
            (int(classroom["id"]),),
        )
        for row in score_tabulation:
            row["assignment_type_label"] = assignment_type_label(row["assignment_type"])
            row["due_label"] = format_iso_label(row["due_at"])
            row["submitted_label"] = format_iso_label(row["submitted_at"]) if row["submitted_at"] else "Not submitted"
            row["timing_status"] = submission_timing_status(row["due_at"], row["submitted_at"])
            row["score_display"] = row["grade"] or "-"

    classmates_query = (
        """
        SELECT u.id, u.name, u.email
        FROM enrollments e
        JOIN users u ON u.id = e.user_id
        WHERE e.classroom_id = ?
        ORDER BY u.name ASC
        """
    )
    learners = fetch_all(classmates_query, (int(classroom["id"]),))
    people = [classroom["mentor_name"]]
    people.extend(item["name"] for item in learners)
    certificate_requests = []
    if user["role"] in {"mentor", "admin"}:
        target = attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8))
        for learner in learners:
            progress = attendance_progress_for_student(int(classroom["id"]), int(learner["id"]), target)
            status = certificate_eligibility_status(int(classroom["id"]), int(learner["id"]), int(progress["attendance_progress"]))
            approval = status.get("approval")
            certificate_requests.append({
                **learner,
                **progress,
                **status,
                "approved_label": format_iso_label(approval["approved_at"]) if approval else "Pending admin approval",
                "eligible_for_approval": status["attendance_complete"]
                and status["all_coursework_submitted"]
                and status["project_passed"],
            })

    if user["role"] == "student":
        project_suggestions = fetch_all(
            """
            SELECT ps.*, u.name AS student_name
            FROM project_suggestions ps
            JOIN users u ON u.id = ps.student_id
            WHERE ps.classroom_id = ? AND ps.student_id = ?
            ORDER BY ps.submitted_at DESC
            """,
            (int(classroom["id"]), int(user["id"])),
        )
    else:
        project_suggestions = fetch_all(
            """
            SELECT ps.*, u.name AS student_name
            FROM project_suggestions ps
            JOIN users u ON u.id = ps.student_id
            WHERE ps.classroom_id = ?
            ORDER BY ps.submitted_at DESC
            """,
            (int(classroom["id"]),),
        )
    for suggestion in project_suggestions:
        suggestion["submitted_label"] = format_iso_label(suggestion["submitted_at"])

    attendance_sessions = fetch_all(
        """
        SELECT
            s.id,
            s.title,
            s.session_date,
            s.created_at,
            creator.name AS created_by_name,
            COUNT(r.id) AS marked_count,
            SUM(CASE WHEN r.status = 'present' THEN 1 ELSE 0 END) AS present_count,
            SUM(CASE WHEN r.status = 'late' THEN 1 ELSE 0 END) AS late_count,
            SUM(CASE WHEN r.status = 'absent' THEN 1 ELSE 0 END) AS absent_count
        FROM attendance_sessions s
        JOIN users creator ON creator.id = s.created_by
        LEFT JOIN attendance_records r ON r.session_id = s.id
        WHERE s.classroom_id = ?
        GROUP BY s.id, creator.name
        ORDER BY s.session_date DESC, s.created_at DESC
        """,
        (int(classroom["id"]),),
    )
    for session in attendance_sessions:
        session["session_label"] = format_iso_label(session["session_date"], include_time=False)

    if user["role"] == "student":
        attendance_history = fetch_all(
            """
            SELECT
                s.title,
                s.session_date,
                r.status,
                r.notes
            FROM attendance_records r
            JOIN attendance_sessions s ON s.id = r.session_id
            WHERE s.classroom_id = ? AND r.student_id = ?
            ORDER BY s.session_date DESC, s.created_at DESC
            """,
            (int(classroom["id"]), int(user["id"])),
        )
        for item in attendance_history:
            item["session_label"] = format_iso_label(item["session_date"], include_time=False)
        attendance_summary_row = fetch_one(
            """
            SELECT
                COUNT(*) AS total_sessions,
                SUM(CASE WHEN r.status = 'present' THEN 1 ELSE 0 END) AS present_count,
                SUM(CASE WHEN r.status = 'late' THEN 1 ELSE 0 END) AS late_count,
                SUM(CASE WHEN r.status = 'absent' THEN 1 ELSE 0 END) AS absent_count
            FROM attendance_records r
            JOIN attendance_sessions s ON s.id = r.session_id
            WHERE s.classroom_id = ? AND r.student_id = ?
            """,
            (int(classroom["id"]), int(user["id"])),
        )
    else:
        attendance_history = fetch_all(
            """
            SELECT
                s.title,
                s.session_date,
                u.name AS student_name,
                r.status,
                r.notes
            FROM attendance_records r
            JOIN attendance_sessions s ON s.id = r.session_id
            JOIN users u ON u.id = r.student_id
            WHERE s.classroom_id = ?
            ORDER BY s.session_date DESC, u.name ASC
            LIMIT 40
            """,
            (int(classroom["id"]),),
        )
        for item in attendance_history:
            item["session_label"] = format_iso_label(item["session_date"], include_time=False)
        attendance_summary_row = fetch_one(
            """
            SELECT
                COUNT(DISTINCT s.id) AS total_sessions,
                SUM(CASE WHEN r.status = 'present' THEN 1 ELSE 0 END) AS present_count,
                SUM(CASE WHEN r.status = 'late' THEN 1 ELSE 0 END) AS late_count,
                SUM(CASE WHEN r.status = 'absent' THEN 1 ELSE 0 END) AS absent_count
            FROM attendance_sessions s
            LEFT JOIN attendance_records r ON r.session_id = s.id
            WHERE s.classroom_id = ?
            """,
            (int(classroom["id"]),),
        )
    # Retrieve and sync Zoho live sessions
    live_sessions = fetch_all(
        "SELECT * FROM live_sessions WHERE classroom_id = ? ORDER BY start_time DESC",
        (int(classroom["id"]),)
    )
    # Convert read-only row dicts to mutable dicts for adding custom fields
    live_sessions = [dict(s) for s in live_sessions]

    now_val = datetime.now(tz=UTC)
    for session in live_sessions:
        rewrite_meeting_links_if_mock(session)
        try:
            start_dt = datetime.fromisoformat(session["start_time"])
            duration_delta = timedelta(minutes=int(session["duration"]))
            end_dt = start_dt + duration_delta

            session_is_completed = now_val > end_dt
            session_is_live = (start_dt - timedelta(minutes=15)) <= now_val <= end_dt
        except Exception as e:
            print(f"Error parsing session times: {e}")
            session_is_completed = False
            session_is_live = False

        session["is_live"] = session_is_live
        session["is_completed"] = session_is_completed
        session["start_time_formatted"] = format_iso_label(session["start_time"])

        # Auto-sync recording if completed and still pending
        # In production, this is skipped for real meetings to avoid slow synchronous API calls during page load.
        if session_is_completed and session.get("recording_status") == "pending":
            is_mock = session["meeting_key"].startswith("mock-") if session.get("meeting_key") else False
            if not IS_PRODUCTION or is_mock:
                try:
                    download_url, play_url = zoho_meeting.fetch_meeting_recording(session["meeting_key"])
                    if download_url or play_url:
                        db_conn = get_db()
                        db_conn.execute(
                            """
                            UPDATE live_sessions
                            SET recording_download_url = ?, recording_play_url = ?, recording_status = 'available'
                            WHERE id = ?
                            """,
                            (download_url, play_url, int(session["id"]))
                        )

                        rec_title = f"Recording: {session['topic']}"
                        existing_material = db_conn.execute(
                            "SELECT id FROM materials WHERE classroom_id = ? AND title = ?",
                            (int(classroom["id"]), rec_title)
                        ).fetchone()

                        if not existing_material:
                            desc = f"Automatically imported recording from live training session on {format_iso_label(session['start_time'])}."
                            db_conn.execute(
                                """
                                INSERT INTO materials (
                                    classroom_id, uploader_id, title, description, material_url, stored_name, original_name, relative_path, uploaded_at
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    int(classroom["id"]),
                                    int(classroom["mentor_id"]),
                                    rec_title,
                                    desc,
                                    play_url or download_url,
                                    "zoho-recording",
                                    f"recording-{session['meeting_key']}",
                                    play_url or download_url,
                                    timestamp_now()
                                )
                            )
                        db_conn.commit()

                        session["recording_download_url"] = download_url
                        session["recording_play_url"] = play_url
                        session["recording_status"] = "available"
                except Exception as e:
                    print(f"Error auto-syncing recording for meeting {session['meeting_key']}: {e}")

    classroom["live_sessions"] = live_sessions

    classroom["announcements"] = announcements
    classroom["modules"] = modules
    classroom["materials"] = materials
    classroom["assignments"] = assignments
    classroom["project_assignments"] = project_assignments
    classroom["project_suggestions"] = project_suggestions
    classroom["score_tabulation"] = score_tabulation
    classroom["learners"] = learners
    classroom["attendance_sessions"] = attendance_sessions
    classroom["attendance_history"] = attendance_history
    if user["role"] in {"mentor", "admin"}:
        classroom["certificate_requests"] = certificate_requests
    classroom["attendance_summary"] = {
        "total_sessions": int(attendance_summary_row["total_sessions"] or 0) if attendance_summary_row else 0,
        "present_count": int(attendance_summary_row["present_count"] or 0) if attendance_summary_row else 0,
        "late_count": int(attendance_summary_row["late_count"] or 0) if attendance_summary_row else 0,
        "absent_count": int(attendance_summary_row["absent_count"] or 0) if attendance_summary_row else 0,
    }
    if user["role"] in {"mentor", "admin"}:
        classroom["mentor_submissions"] = mentor_submissions
    classroom["people"] = people
    classroom["members"] = int(classroom["member_count"])
    classroom["assignments_due"] = len(assignments)
    return classroom


@app.context_processor
def inject_site_context() -> dict[str, Any]:
    return {
        "current_year": datetime.now(tz=UTC).year,
        "ga_tracking_id": app.config.get("GA_TRACKING_ID"),
        "lms_nav_label": "Learning Hub",
        "lms_nav_url": url_for("dashboard") if g.get("user") else url_for("lms"),
        "current_user": g.get("user"),
        "form_started_at": f"{time.time():.3f}",
        "csrf_token": get_csrf_token,
        "csrf_field": csrf_field,
        "PROJECT_UNLOCK_PROGRESS": PROJECT_UNLOCK_PROGRESS,
    }


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
        lms_features=LMS_FEATURES,
        featured_classrooms=get_public_classrooms(),
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


@app.route("/registration", methods=["GET", "POST"])
def registration():
    if request.method == "POST":
        if public_form_blocked("registration", limit=4, min_seconds=5):
            return redirect(url_for("registration"))

        name = request.form.get("name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        phone = request.form.get("phone", "").strip()
        highest_qualification = request.form.get("highest_qualification", "").strip()
        discipline = request.form.get("discipline", "").strip()
        skill_level = request.form.get("skill_level", "").strip()
        past_training = request.form.get("past_training", "").strip()
        preferred_course = request.form.get("preferred_course", "").strip()
        preferred_days = [day.strip() for day in request.form.getlist("preferred_days") if day.strip()]
        preferred_start_date = request.form.get("preferred_start_date", "").strip()
        preferred_time_slot = request.form.get("preferred_time_slot", "").strip()
        schedule_acknowledgement = request.form.get("schedule_acknowledgement", "").strip()
        pc_only_acknowledgement = request.form.get("pc_only_acknowledgement", "").strip()
        commitment_acknowledgement = request.form.get("commitment_acknowledgement", "").strip()
        outside_hours_acknowledgement = request.form.get("outside_hours_acknowledgement", "").strip()
        has_pc = request.form.get("has_pc", "").strip()
        has_internet = request.form.get("has_internet", "").strip()
        additional_comments = request.form.get("additional_comments", "").strip()
        receipt_file = request.files.get("payment_receipt")

        if not all(
            [
                name,
                email,
                phone,
                highest_qualification,
                discipline,
                skill_level,
                past_training,
                preferred_course,
                preferred_start_date,
                preferred_time_slot,
                schedule_acknowledgement,
                pc_only_acknowledgement,
                commitment_acknowledgement,
                outside_hours_acknowledgement,
                has_pc,
                has_internet,
                additional_comments,
            ]
        ):
            flash("Please complete every required field in the registration form.", "error")
            return redirect(url_for("registration"))

        if not is_valid_email(email):
            flash("Please provide a valid email address.", "error")
            return redirect(url_for("registration"))

        if too_many_links(additional_comments, max_links=1):
            flash("Please remove extra links from the registration comments.", "error")
            return redirect(url_for("registration"))

        if highest_qualification not in ACADEMIC_QUALIFICATIONS:
            flash("Please choose a valid academic qualification.", "error")
            return redirect(url_for("registration"))

        if skill_level not in {"1", "2", "3", "4", "5"}:
            flash("Please rate your current data analysis skill level from 1 to 5.", "error")
            return redirect(url_for("registration"))

        if preferred_course not in COURSE_OPTIONS:
            flash("Please select one preferred course.", "error")
            return redirect(url_for("registration"))

        if len(preferred_days) != 2 or any(day not in TRAINING_DAYS for day in preferred_days):
            flash("Please select exactly two preferred training days.", "error")
            return redirect(url_for("registration"))

        if schedule_acknowledgement not in {"I understand and agree", "I don't know and I disagree"}:
            flash("Please confirm whether you understand the training schedule.", "error")
            return redirect(url_for("registration"))

        if pc_only_acknowledgement not in {"I agree", "I disagree"}:
            flash("Please confirm the PC-only training requirement.", "error")
            return redirect(url_for("registration"))

        if commitment_acknowledgement not in {"YES", "NO"}:
            flash("Please confirm your training commitment level.", "error")
            return redirect(url_for("registration"))

        if outside_hours_acknowledgement not in {"YES", "NO"}:
            flash("Please confirm whether you can allocate time outside regular work hours.", "error")
            return redirect(url_for("registration"))

        if has_pc not in {"YES", "NO"}:
            flash("Please confirm whether you have access to a personal computer.", "error")
            return redirect(url_for("registration"))

        if has_internet not in {"YES", "NO"}:
            flash("Please confirm whether you have access to stable internet.", "error")
            return redirect(url_for("registration"))

        if receipt_file is None or not receipt_file.filename:
            flash("Please upload your payment receipt or evidence before submitting.", "error")
            return redirect(url_for("registration"))

        try:
            receipt_info = store_uploaded_file(receipt_file, REGISTRATION_RECEIPTS_DIR, "training_registration")
            append_csv_row(
                REGISTRATION_FILE,
                [
                    timestamp_now(),
                    name,
                    email,
                    phone,
                    highest_qualification,
                    discipline,
                    skill_level,
                    past_training,
                    preferred_course,
                    " | ".join(preferred_days),
                    preferred_start_date,
                    preferred_time_slot,
                    schedule_acknowledgement,
                    pc_only_acknowledgement,
                    commitment_acknowledgement,
                    outside_hours_acknowledgement,
                    has_pc,
                    has_internet,
                    additional_comments,
                    receipt_info["original_name"],
                    receipt_info["relative_path"],
                    request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    request.headers.get("User-Agent", "")[:200],
                ],
            )

            send_form_notification(
                subject=f"New Data Analysis Training Registration: {name}",
                body=(
                    f"Name: {name}\n"
                    f"Email: {email}\n"
                    f"Phone: {phone}\n"
                    f"Qualification: {highest_qualification}\n"
                    f"Discipline: {discipline}\n"
                    f"Skill level: {skill_level}\n"
                    f"Preferred course: {preferred_course}\n"
                    f"Preferred days: {', '.join(preferred_days)}\n"
                    f"Preferred start date: {preferred_start_date}\n"
                    f"Preferred time slot: {preferred_time_slot}\n"
                    f"Receipt file: {receipt_info['original_name']}\n"
                    f"Receipt path: {receipt_info['relative_path']}\n"
                ),
            )
            acknowledgement_sent = send_registration_acknowledgement_email(
                recipient_name=name,
                recipient_email=email,
                preferred_course=preferred_course,
            )
        except Exception as exc:
            app.logger.error(f"Error saving registration form: {exc}")
            flash("We could not submit your registration right now. Please try again shortly.", "error")
            return redirect(url_for("registration"))

        if acknowledgement_sent:
            flash("Your registration is complete. A confirmation email has been sent, and we will communicate the next steps to you soon.", "success")
        else:
            flash("Your registration is complete. We will communicate the next steps to you soon.", "success")
        return redirect(url_for("registration"))

    return render_template(
        "registration.html",
        page_title="Data Analysis Training Registration | Tektutors",
        meta_description="Register for Tektutors data analysis training and upload your payment receipt.",
        active_page="contact",
        academic_qualifications=ACADEMIC_QUALIFICATIONS,
        course_options=COURSE_OPTIONS,
        training_days=TRAINING_DAYS,
    )


@app.route("/lms")
def lms():
    return render_template(
        "lms.html",
        page_title="Tektutors Learning Hub | Classroom Experience",
        meta_description="Explore the Tektutors Learning Hub: classroom streams, assignments, modules, mentor feedback, and learner progress in one place.",
        active_page="lms",
        classrooms=get_public_classrooms(),
        overview_stats=get_lms_overview_stats(),
        lms_features=LMS_FEATURES,
    )


@app.route("/auth/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        flash(
            "Public Learning Hub signup is disabled. Access is created by the admin after confirming your class registration and eligibility.",
            "error",
        )
        return redirect(url_for("contact"))

    return render_template(
        "register.html",
        page_title="Learning Hub Access | Tektutors",
        meta_description="Learning Hub access is created by the admin after confirming class registration and eligibility.",
        active_page="lms",
    )


@app.route("/auth/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        if public_form_rate_limited("login", limit=12):
            flash("Too many sign-in attempts. Please wait a few minutes and try again.", "error")
            return redirect(url_for("login"))

        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        user = get_user_by_email(email)

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("We could not match that email and password.", "error")
            return redirect(url_for("login"))
        if not is_user_approved(user):
            flash("Your Learning Hub access is not active yet. Please wait for the admin to approve your registration and send your login details.", "error")
            return redirect(url_for("login"))
        if is_user_suspended(user):
            flash("Account suspended. Contact the admin.", "error")
            return redirect(url_for("login"))

        login_user(user)
        flash("Signed in successfully.", "success")
        next_path = request.args.get("next")
        if next_path:
            return redirect(next_path)
        return redirect(url_for("dashboard"))

    return render_template(
        "login.html",
        page_title="Sign in | Tektutors Learning Hub",
        meta_description="Sign in to your Tektutors mentor or learner account.",
        active_page="lms",
    )


@app.route("/auth/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if public_form_rate_limited("forgot_password", limit=5):
            flash("Too many reset requests. Please wait a few minutes and try again.", "error")
            return redirect(url_for("forgot_password"))

        email = normalize_email(request.form.get("email", ""))
        if not is_valid_email(email):
            flash("Please provide a valid email address.", "error")
            return redirect(url_for("forgot_password"))

        user = get_user_by_email(email)
        if user and is_user_approved(user) and not is_user_suspended(user):
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(tz=UTC) + timedelta(minutes=30)).isoformat()
            get_db().execute(
                """
                UPDATE users
                SET reset_token = ?, reset_token_expires_at = ?
                WHERE id = ?
                """,
                (token, expires_at, int(user["id"])),
            )
            get_db().commit()
            try:
                send_password_reset_email(
                    recipient_name=user["name"],
                    recipient_email=user["email"],
                    reset_url=url_for("reset_password", token=token, _external=True),
                )
            except RuntimeError as exc:
                flash(str(exc), "error")
                return redirect(url_for("forgot_password"))
        flash("If that email exists in the Learning Hub, a reset link has been sent.", "success")
        return redirect(url_for("login"))

    return render_template(
        "forgot_password.html",
        page_title="Forgot Password | Tektutors Learning Hub",
        meta_description="Request a password reset for your Tektutors Learning Hub account.",
        active_page="lms",
    )


@app.route("/auth/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    user = fetch_one(
        "SELECT * FROM users WHERE reset_token = ?",
        (token,),
    )
    if user is None or password_reset_expired(user.get("reset_token_expires_at")):
        flash("That reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(password) < 8:
            flash("Choose a password with at least 8 characters.", "error")
            return redirect(url_for("reset_password", token=token))
        if password != confirm_password:
            flash("Password confirmation does not match.", "error")
            return redirect(url_for("reset_password", token=token))

        get_db().execute(
            """
            UPDATE users
            SET password_hash = ?, must_change_password = 0, reset_token = NULL, reset_token_expires_at = NULL
            WHERE id = ?
            """,
            (generate_password_hash(password), int(user["id"])),
        )
        get_db().commit()
        flash("Your password has been reset. You can now sign in.", "success")
        return redirect(url_for("login"))

    return render_template(
        "reset_password.html",
        page_title="Reset Password | Tektutors Learning Hub",
        meta_description="Reset your Tektutors Learning Hub password.",
        active_page="lms",
        token=token,
        heading="Create your new password",
        intro="Set a new password for your Learning Hub account to continue.",
        submit_label="Save new password",
    )


@app.route("/account/password", methods=["GET", "POST"])
@login_required()
def account_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not must_change_password(g.user):
            if not check_password_hash(g.user["password_hash"], current_password):
                flash("Your current password is not correct.", "error")
                return redirect(url_for("account_password"))
        if len(new_password) < 8:
            flash("Choose a password with at least 8 characters.", "error")
            return redirect(url_for("account_password"))
        if new_password != confirm_password:
            flash("Password confirmation does not match.", "error")
            return redirect(url_for("account_password"))

        get_db().execute(
            """
            UPDATE users
            SET password_hash = ?, must_change_password = 0, reset_token = NULL, reset_token_expires_at = NULL
            WHERE id = ?
            """,
            (generate_password_hash(new_password), int(g.user["id"])),
        )
        get_db().commit()
        flash("Your password has been updated successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "account_password.html",
        page_title="Update Password | Tektutors Learning Hub",
        meta_description="Update your Tektutors Learning Hub password.",
        active_page="lms",
        require_current_password=not must_change_password(g.user),
    )


@app.route("/auth/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required()
def dashboard():
    if g.user["role"] in ("admin", "growth_manager", "corporate_staff", "finance"):
        return redirect(url_for("admin_dashboard"))
    if g.user["role"] == "mentor":
        return redirect(url_for("mentor_dashboard"))
    if g.user["role"] == "growth_associate":
        return redirect(url_for("growth_associate_dashboard"))
    return redirect(url_for("student_dashboard"))


@app.route("/dashboard/admin")
@login_required("admin")
def admin_dashboard():
    return render_template(
        "dashboard_admin.html",
        page_title="Admin Dashboard | Tektutors Learning Hub",
        meta_description="Manage Learning Hub users, classrooms, activities, and course resources across Tektutors.",
        active_page="lms",
        dashboard_data=get_admin_dashboard_data(),
        generated_password=session.pop("generated_password", None),
    )


@app.route("/dashboard/student")
@login_required("student")
def student_dashboard():
    pending_feedback = get_pending_course_feedback(int(g.user["id"]))
    session["has_pending_feedback"] = (pending_feedback is not None)
    if pending_feedback is not None:
        return render_template(
            "course_feedback.html",
            page_title="Course Feedback Required | Tektutors Learning Hub",
            meta_description="Share quick course feedback before continuing in the Learning Hub.",
            active_page="lms",
            pending_feedback=pending_feedback,
            rating_fields=FEEDBACK_RATING_FIELDS,
        )
    return render_template(
        "dashboard_student.html",
        page_title="Learner Dashboard | Tektutors Learning Hub",
        meta_description="Track your Tektutors classes, assignments, and announcements.",
        active_page="lms",
        dashboard_data=get_student_dashboard_data(int(g.user["id"])),
    )


@app.route("/dashboard/student/certificates/<slug>/download")
@login_required("student")
def download_student_certificate(slug: str):
    classroom = fetch_one(
        """
        SELECT
            c.id,
            c.slug,
            c.title,
            c.code,
            c.course_duration,
            c.attendance_target,
            mentor.name AS mentor_name
        FROM classrooms c
        JOIN users mentor ON mentor.id = c.mentor_id
        JOIN enrollments e ON e.classroom_id = c.id
        WHERE c.slug = ? AND e.user_id = ?
        """,
        (slug, int(g.user["id"])),
    )
    if classroom is None:
        flash("That certificate could not be found.", "error")
        return redirect(url_for("student_dashboard"))

    progress = attendance_progress_for_student(
        int(classroom["id"]),
        int(g.user["id"]),
        attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
    )
    status = certificate_eligibility_status(int(classroom["id"]), int(g.user["id"]), int(progress["attendance_progress"]))
    if not status["attendance_complete"]:
        flash("Your certificate unlocks when your attendance progress reaches 100%.", "error")
        return redirect(url_for("student_dashboard"))
    if not status["all_coursework_submitted"]:
        flash("Your certificate unlocks after you submit every assignment and project.", "error")
        return redirect(url_for("student_dashboard"))
    if not status["project_passed"]:
        flash("Your certificate unlocks after you pass the project.", "error")
        return redirect(url_for("student_dashboard"))
    if not status["admin_approved"]:
        flash("Your certificate is awaiting admin approval before download access is enabled.", "error")
        return redirect(url_for("student_dashboard"))

    issued_at = datetime.now(tz=UTC)
    certificate_id = f"TT-{int(g.user['id']):04d}-{int(classroom['id']):04d}"
    verify_url = url_for("verify_certificate", certificate_id=certificate_id, _external=True)
    courses_rows = fetch_all(
        """
        SELECT c.title FROM classrooms c
        JOIN enrollments e ON e.classroom_id = c.id
        WHERE e.user_id = ?
        """,
        (int(g.user["id"]),),
    )
    courses = [row["title"] for row in courses_rows]
    if not courses:
        # Fallback to the current classroom title
        courses = [classroom["title"]]
    # Optional: log for debugging
    app.logger.debug(f"Certificate generation for user {g.user['id']}: courses={courses}")

    # Ensure the latest template is used
    app.jinja_env.cache = {}
    html = render_template(
        "certificate.html",
        learner_name=g.user["name"],
        classroom=classroom,
        courses=courses,
        issued_label=issued_at.strftime("%B %d, %Y"),
        certificate_id=certificate_id,
        verify_url=verify_url,
        logo_url=url_for("static", filename="images/tektutors-logo.svg", _external=True),
        qr_url=f"https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={verify_url}",
        template_url=url_for("static", filename="images/tektutors-certificate-template.png", _external=True),
        is_sample=False,
        is_earned=True,
    )
    app.logger.debug('Rendered certificate HTML: %s', html)
    response = make_response(html)
    filename = secure_filename(f"tektutors-certificate-{classroom['slug']}-{g.user['name']}.html")
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    # Prevent any caching of the downloaded certificate HTML
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/dashboard/student/certificates/<slug>/view")
@login_required("student")
def view_student_certificate(slug: str):
    """Render the learner's certificate HTML for embedding in the certificate tab."""
    classroom = fetch_one(
        """
        SELECT
            c.id,
            c.slug,
            c.title,
            c.code,
            c.course_duration,
            c.attendance_target,
            mentor.name AS mentor_name
        FROM classrooms c
        JOIN users mentor ON mentor.id = c.mentor_id
        JOIN enrollments e ON e.classroom_id = c.id
        WHERE c.slug = ? AND e.user_id = ?
        """,
        (slug, int(g.user["id"]))
    )
    if classroom is None:
        flash("That certificate could not be found.", "error")
        return redirect(url_for("student_dashboard"))

    progress = attendance_progress_for_student(
        int(classroom["id"]),
        int(g.user["id"]),
        attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
    )
    status = certificate_eligibility_status(int(classroom["id"]), int(g.user["id"]), int(progress["attendance_progress"]))
    all_requirements_met = (
        status.get("attendance_complete")
        and status.get("all_coursework_submitted")
        and status.get("project_passed")
        and status.get("admin_approved")
    )
    is_sample = not all_requirements_met
    is_earned = all_requirements_met

    issued_at = datetime.now(tz=UTC)
    certificate_id = f"TT-{int(g.user['id']):04d}-{int(classroom['id']):04d}"
    verify_url = url_for("verify_certificate", certificate_id=certificate_id, _external=True)
    courses_rows = fetch_all(
        """
        SELECT c.title FROM classrooms c
        JOIN enrollments e ON e.classroom_id = c.id
        WHERE e.user_id = ?
        """,
        (int(g.user["id"]),),
    )
    courses = [row["title"] for row in courses_rows] or [classroom["title"]]
    html = render_template(
        "certificate.html",
        learner_name=g.user["name"],
        classroom=classroom,
        courses=courses,
        issued_label=issued_at.strftime("%B %d, %Y"),
        certificate_id=certificate_id,
        verify_url=verify_url,
        logo_url=url_for("static", filename="images/tektutors-logo.svg", _external=True),
        qr_url=f"https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={verify_url}",
        template_url=url_for("static", filename="images/tektutors-certificate-template.png", _external=True),
        is_sample=is_sample,
        is_earned=is_earned,
    )
    response = make_response(html)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


@app.route("/certificates/verify/<certificate_id>")
def verify_certificate(certificate_id: str):
    match = re.fullmatch(r"TT-(\d{4})-(\d{4})", certificate_id.strip().upper())
    if not match:
        return render_template(
            "certificate_verify.html",
            page_title="Certificate Verification | Tektutors",
            active_page="lms",
            certificate=None,
        ), 404

    user_id = int(match.group(1))
    classroom_id = int(match.group(2))
    certificate = fetch_one(
        """
        SELECT
            learner.name AS learner_name,
            c.title AS classroom_title,
            c.course_duration,
            mentor.name AS mentor_name,
            c.attendance_target
        FROM classrooms c
        JOIN enrollments e ON e.classroom_id = c.id
        JOIN users learner ON learner.id = e.user_id
        JOIN users mentor ON mentor.id = c.mentor_id
        WHERE learner.id = ? AND c.id = ?
        """,
        (user_id, classroom_id),
    )
    if certificate is not None:
        progress = attendance_progress_for_student(
            classroom_id,
            user_id,
            attendance_target_from_duration(certificate.get("course_duration"), int(certificate["attendance_target"] or 8)),
        )
        status = certificate_eligibility_status(classroom_id, user_id, int(progress["attendance_progress"]))
        if not status["certificate_unlocked"]:
            certificate = None

    return render_template(
        "certificate_verify.html",
        page_title="Certificate Verification | Tektutors",
        active_page="lms",
        certificate=certificate,
        certificate_id=certificate_id.strip().upper(),
    ), 200 if certificate else 404


@app.route("/admin/classrooms/<slug>/certificates/<int:student_id>/approval", methods=["POST"])
@login_required("admin")
def admin_update_certificate_approval(slug: str, student_id: int):
    ensure_certificate_approvals_table(get_db())
    classroom = fetch_one("SELECT * FROM classrooms WHERE slug = ?", (slug,))
    if classroom is None:
        flash("That classroom could not be found.", "error")
        return redirect(url_for("admin_dashboard"))

    learner = fetch_one(
        """
        SELECT u.id, u.name
        FROM enrollments e
        JOIN users u ON u.id = e.user_id
        WHERE e.classroom_id = ? AND u.id = ? AND u.role = 'student'
        """,
        (int(classroom["id"]), student_id),
    )
    if learner is None:
        flash("That learner is not enrolled in this classroom.", "error")
        return redirect(url_for("lms_classroom", slug=slug) + "#certificate")

    action = request.form.get("action", "approve").strip().lower()
    if action == "revoke":
        get_db().execute(
            """
            UPDATE certificate_approvals
            SET revoked_at = ?, revoked_by = ?
            WHERE classroom_id = ? AND student_id = ? AND revoked_at IS NULL
            """,
            (timestamp_now(), int(g.user["id"]), int(classroom["id"]), student_id),
        )
        get_db().commit()
        flash(f"Certificate download access revoked for {learner['name']}.", "success")
        return redirect(url_for("lms_classroom", slug=slug) + "#certificate")

    progress = attendance_progress_for_student(
        int(classroom["id"]),
        student_id,
        attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
    )
    status = certificate_eligibility_status(int(classroom["id"]), student_id, int(progress["attendance_progress"]))
    if not (status["attendance_complete"] and status["all_coursework_submitted"] and status["project_passed"]):
        flash("This learner is not ready for certificate approval yet.", "error")
        return redirect(url_for("lms_classroom", slug=slug) + "#certificate")

    get_db().execute(
        """
        INSERT INTO certificate_approvals (classroom_id, student_id, approved_by, approved_at, revoked_at, revoked_by)
        VALUES (?, ?, ?, ?, NULL, NULL)
        ON CONFLICT(classroom_id, student_id) DO UPDATE SET
            approved_by = excluded.approved_by,
            approved_at = excluded.approved_at,
            revoked_at = NULL,
            revoked_by = NULL
        """,
        (int(classroom["id"]), student_id, int(g.user["id"]), timestamp_now()),
    )
    get_db().commit()
    flash(f"Certificate download access approved for {learner['name']}.", "success")
    return redirect(url_for("lms_classroom", slug=slug) + "#certificate")


@app.route("/course-feedback", methods=["POST"])
@login_required("student")
def submit_course_feedback():
    pending_feedback = get_pending_course_feedback(int(g.user["id"]))
    if pending_feedback is None:
        flash("There is no course feedback due right now.", "success")
        return redirect(url_for("student_dashboard"))

    ratings: dict[str, int] = {}
    for field_name, _label in FEEDBACK_RATING_FIELDS:
        rating = parse_feedback_rating(field_name)
        if rating is None:
            flash("Please rate every feedback area from 1 to 5.", "error")
            return redirect(url_for("student_dashboard"))
        ratings[field_name] = rating

    worst_experience = request.form.get("worst_experience", "").strip()
    best_experience = request.form.get("best_experience", "").strip()
    improvement_suggestion = request.form.get("improvement_suggestion", "").strip()
    if not worst_experience or not best_experience or not improvement_suggestion:
        flash("Please complete the three short experience questions.", "error")
        return redirect(url_for("student_dashboard"))

    if any(len(value) > 700 for value in (worst_experience, best_experience, improvement_suggestion)):
        flash("Please keep each written answer under 700 characters.", "error")
        return redirect(url_for("student_dashboard"))

    score_total = sum(ratings.values())
    score_percentage = rating_percentage(score_total)
    try:
        get_db().execute(
            """
            INSERT INTO course_feedback (
                classroom_id, student_id, milestone, attendance_count,
                course_experience, course_content, mentor_methodology,
                mentor_experience, mentor_knowledge, lms_ease,
                worst_experience, best_experience, improvement_suggestion,
                score_total, score_percentage, submitted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(pending_feedback["id"]),
                int(g.user["id"]),
                int(pending_feedback["milestone"]),
                int(pending_feedback["attendance_count"]),
                ratings["course_experience"],
                ratings["course_content"],
                ratings["mentor_methodology"],
                ratings["mentor_experience"],
                ratings["mentor_knowledge"],
                ratings["lms_ease"],
                worst_experience,
                best_experience,
                improvement_suggestion,
                score_total,
                score_percentage,
                timestamp_now(),
            ),
        )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        session["has_pending_feedback"] = (get_pending_course_feedback(int(g.user["id"])) is not None)
        flash("This feedback checkpoint has already been completed.", "success")
        return redirect(url_for("student_dashboard"))

    session["has_pending_feedback"] = (get_pending_course_feedback(int(g.user["id"])) is not None)
    flash("Thank you. Your course feedback has been submitted.", "success")
    return redirect(url_for("student_dashboard"))


@app.route("/dashboard/mentor")
@login_required("mentor")
def mentor_dashboard():
    return render_template(
        "dashboard_mentor.html",
        page_title="Mentor dashboard | Tektutors Learning Hub",
        meta_description="Manage classrooms, announcements, and submissions in the Tektutors Learning Hub.",
        active_page="lms",
        dashboard_data=get_mentor_dashboard_data(int(g.user["id"])),
    )


@app.route("/admin/users/manage", methods=["POST"])
@login_required("admin")
def admin_manage_user():
    name = request.form.get("name", "").strip()
    email = normalize_email(request.form.get("email", ""))
    role = request.form.get("role", "student").strip().lower()
    send_email = request.form.get("send_email") == "yes"
    password = request.form.get("password", "").strip() or generate_temporary_password()

    if not name or not email:
        flash("Provide both a full name and email address.", "error")
        return redirect(url_for("admin_dashboard"))
    if role not in {"student", "mentor", "admin"}:
        flash("Choose a valid Learning Hub role.", "error")
        return redirect(url_for("admin_dashboard"))
    if not is_valid_email(email):
        flash("Please provide a valid email address.", "error")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    existing_user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    approved_at = timestamp_now()

    if existing_user:
        db.execute(
            """
            UPDATE users
            SET
                name = ?,
                password_hash = ?,
                role = ?,
                is_approved = 1,
                is_suspended = 0,
                suspended_at = NULL,
                approved_at = ?,
                must_change_password = 1,
                reset_token = NULL,
                reset_token_expires_at = NULL
            WHERE email = ?
            """,
            (name, generate_password_hash(password), role, approved_at, email),
        )
        action = "updated"
    else:
        db.execute(
            """
            INSERT INTO users (
                name, email, password_hash, role, is_approved, approved_at, created_at,
                must_change_password, reset_token, reset_token_expires_at, is_suspended, suspended_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, 1, NULL, NULL, 0, NULL)
            """,
            (name, email, generate_password_hash(password), role, approved_at, approved_at),
        )
        action = "created"

    db.commit()
    session["generated_password"] = {
        "name": name,
        "email": email,
        "password": password,
        "role": role,
    }
    flash(f"User {action} and approved successfully.", "success")

    if send_email:
        try:
            send_login_details_email(
                recipient_name=name,
                recipient_email=email,
                password=password,
                role=role,
                login_url=url_for("login", _external=True),
            )
            flash("Login details were sent by email.", "success")
        except RuntimeError as exc:
            flash(str(exc), "error")

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/update", methods=["POST"])
@login_required("admin")
def admin_update_user(user_id: int):
    name = request.form.get("name", "").strip()
    email = normalize_email(request.form.get("email", ""))
    role = request.form.get("role", "student").strip().lower()

    if not name or not email:
        flash("Provide both a full name and email address.", "error")
        return redirect(url_for("admin_dashboard"))
    if role not in {"student", "mentor", "admin"}:
        flash("Choose a valid Learning Hub role.", "error")
        return redirect(url_for("admin_dashboard"))
    if not is_valid_email(email):
        flash("Please provide a valid email address.", "error")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    user = fetch_one("SELECT id, email FROM users WHERE id = ?", (user_id,))
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("admin_dashboard"))

    if email != user["email"]:
        existing = fetch_one("SELECT id FROM users WHERE email = ?", (email,))
        if existing:
            flash("That email is already in use by another user.", "error")
            return redirect(url_for("admin_dashboard"))

    db.execute(
        """
        UPDATE users
        SET name = ?, email = ?, role = ?
        WHERE id = ?
        """,
        (name, email, role, user_id),
    )
    db.commit()
    flash(f"User {name} updated successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/suspension", methods=["POST"])
@login_required("admin")
def admin_update_user_suspension(user_id: int):
    action = request.form.get("action", "").strip().lower()
    user = fetch_one("SELECT id, name, role, is_suspended FROM users WHERE id = ?", (user_id,))
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("admin_dashboard"))

    if user["role"] not in {"student", "mentor"}:
        flash("Only student and mentor accounts can be suspended from this action.", "error")
        return redirect(url_for("admin_dashboard"))

    if int(user["id"]) == int(g.user["id"]):
        flash("You cannot suspend your own active account.", "error")
        return redirect(url_for("admin_dashboard"))

    if action == "suspend":
        get_db().execute(
            "UPDATE users SET is_suspended = 1, suspended_at = ?, reset_token = NULL, reset_token_expires_at = NULL WHERE id = ?",
            (timestamp_now(), user_id),
        )
        flash(f"{user['name']} has been suspended.", "success")
    elif action == "reactivate":
        get_db().execute(
            "UPDATE users SET is_suspended = 0, suspended_at = NULL WHERE id = ?",
            (user_id,),
        )
        flash(f"{user['name']} has been reactivated.", "success")
    else:
        flash("Choose a valid suspension action.", "error")
        return redirect(url_for("admin_dashboard"))

    get_db().commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required("admin")
def admin_delete_user(user_id: int):
    db = get_db()
    user = fetch_one("SELECT id, name, role FROM users WHERE id = ?", (user_id,))
    if user is None:
        flash("User not found.", "error")
        return redirect(url_for("admin_dashboard"))

    if user["role"] not in {"student", "mentor"}:
        flash("Only student and mentor accounts can be removed from this action.", "error")
        return redirect(url_for("admin_dashboard"))

    if int(user["id"]) == int(g.user["id"]):
        flash("You cannot remove your own active account.", "error")
        return redirect(url_for("admin_dashboard"))

    if user["role"] == "mentor":
        assigned_classrooms = db.execute(
            "SELECT COUNT(*) FROM classrooms WHERE mentor_id = ?",
            (user_id,),
        ).fetchone()[0]
        if int(assigned_classrooms):
            flash("Reassign this mentor's classrooms before removing the mentor account.", "error")
            return redirect(url_for("admin_dashboard"))

        db.execute("UPDATE materials SET uploader_id = ? WHERE uploader_id = ?", (int(g.user["id"]), user_id))
        db.execute(
            "UPDATE attendance_sessions SET created_by = ? WHERE created_by = ?",
            (int(g.user["id"]), user_id),
        )
    else:
        submission_files = db.execute(
            "SELECT relative_path FROM submissions WHERE student_id = ? AND relative_path IS NOT NULL",
            (user_id,),
        ).fetchall()
        for submission_file in submission_files:
            delete_uploaded_file(submission_file["relative_path"])

        db.execute("DELETE FROM attendance_records WHERE student_id = ?", (user_id,))
        db.execute("DELETE FROM submissions WHERE student_id = ?", (user_id,))
        db.execute("DELETE FROM enrollments WHERE user_id = ?", (user_id,))

    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash(f"{user['role'].title()} {user['name']} removed successfully.", "success")
    return redirect(url_for("admin_dashboard"))


def parse_classroom_form(existing_slug: str | None = None) -> dict[str, Any] | None:
    title = request.form.get("title", "").strip()
    code = request.form.get("code", "").strip().upper()
    slug = (existing_slug or request.form.get("slug", "")).strip().lower()
    cohort = request.form.get("cohort", "").strip()
    schedule = request.form.get("schedule", "").strip()
    course_duration = request.form.get("course_duration", "").strip() or "4 weeks"
    course_start_date = request.form.get("course_start_date", "").strip()
    theme = request.form.get("theme", "").strip().lower() or "analytics"
    description = request.form.get("description", "").strip()
    hero_metric = request.form.get("hero_metric", "").strip() or "0% average progress"
    live_sessions = request.form.get("live_sessions", "0").strip() or "0"
    mentor_id = request.form.get("mentor_id", "").strip()

    if not all([title, code, slug, cohort, schedule, description, mentor_id]):
        flash("Complete all classroom fields before saving.", "error")
        return None
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        flash("Use lowercase letters, numbers, and hyphens for the classroom slug.", "error")
        return None
    if course_start_date:
        try:
            datetime.fromisoformat(course_start_date)
        except ValueError:
            flash("Choose a valid course start date.", "error")
            return None

    mentor = fetch_one("SELECT id FROM users WHERE id = ? AND role = 'mentor'", (mentor_id,))
    if mentor is None:
        flash("Select a valid mentor for the classroom.", "error")
        return None

    try:
        live_sessions_value = int(live_sessions)
    except ValueError:
        flash("Live sessions must be a whole number.", "error")
        return None

    return {
        "title": title,
        "code": code,
        "slug": slug,
        "cohort": cohort,
        "schedule": schedule,
        "course_duration": course_duration,
        "course_start_date": course_start_date or timestamp_now()[:10],
        "theme": theme,
        "description": description,
        "hero_metric": hero_metric,
        "live_sessions": live_sessions_value,
        "mentor_id": int(mentor_id),
    }


@app.route("/admin/classrooms/create", methods=["POST"])
@login_required("admin")
def admin_create_classroom():
    classroom_data = parse_classroom_form()
    if classroom_data is None:
        return redirect(url_for("admin_dashboard"))

    try:
        get_db().execute(
            """
            INSERT INTO classrooms (
                slug, title, code, cohort, schedule, course_duration, course_start_date, theme, description, hero_metric,
                completion, live_sessions, mentor_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                classroom_data["slug"],
                classroom_data["title"],
                classroom_data["code"],
                classroom_data["cohort"],
                classroom_data["schedule"],
                classroom_data["course_duration"],
                classroom_data["course_start_date"],
                classroom_data["theme"],
                classroom_data["description"],
                classroom_data["hero_metric"],
                classroom_data["live_sessions"],
                classroom_data["mentor_id"],
                timestamp_now(),
            ),
        )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        flash("That classroom code or slug already exists. Choose a unique one.", "error")
        return redirect(url_for("admin_dashboard"))
    flash("Classroom created successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/classrooms/<slug>/update", methods=["POST"])
@login_required("admin")
def admin_update_classroom(slug: str):
    classroom = fetch_one("SELECT id, slug, title FROM classrooms WHERE slug = ?", (slug,))
    if classroom is None:
        flash("That classroom could not be found.", "error")
        return redirect(url_for("admin_dashboard"))

    classroom_data = parse_classroom_form(existing_slug=slug)
    if classroom_data is None:
        return redirect(url_for("admin_dashboard"))

    try:
        get_db().execute(
            """
            UPDATE classrooms
            SET
                title = ?,
                code = ?,
                cohort = ?,
                schedule = ?,
                course_duration = ?,
                course_start_date = ?,
                theme = ?,
                description = ?,
                hero_metric = ?,
                live_sessions = ?,
                mentor_id = ?
            WHERE id = ?
            """,
            (
                classroom_data["title"],
                classroom_data["code"],
                classroom_data["cohort"],
                classroom_data["schedule"],
                classroom_data["course_duration"],
                classroom_data["course_start_date"],
                classroom_data["theme"],
                classroom_data["description"],
                classroom_data["hero_metric"],
                classroom_data["live_sessions"],
                classroom_data["mentor_id"],
                int(classroom["id"]),
            ),
        )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        flash("That classroom code is already in use. Choose a unique one.", "error")
        return redirect(url_for("admin_dashboard"))

    flash(f"{classroom_data['title']} updated successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/classrooms/<slug>/enroll", methods=["POST"])
@login_required("admin")
def admin_enroll_student(slug: str):
    classroom = fetch_one("SELECT id, title FROM classrooms WHERE slug = ?", (slug,))
    student_id = request.form.get("student_id", "").strip()
    if classroom is None:
        flash("That classroom could not be found.", "error")
        return redirect(url_for("admin_dashboard"))

    student = fetch_one("SELECT id FROM users WHERE id = ? AND role = 'student'", (student_id,))
    if student is None:
        flash("Select a valid learner to enroll.", "error")
        return redirect(url_for("admin_dashboard"))

    get_db().execute(
        "INSERT OR IGNORE INTO enrollments (classroom_id, user_id, created_at) VALUES (?, ?, ?)",
        (int(classroom["id"]), int(student_id), timestamp_now()),
    )
    get_db().commit()
    flash(f"Learner enrolled in {classroom['title']}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/classrooms/<slug>/learners/<int:student_id>/remove", methods=["POST"])
@login_required("admin")
def admin_remove_classroom_learner(slug: str, student_id: int):
    classroom = fetch_one("SELECT id, title FROM classrooms WHERE slug = ?", (slug,))
    if classroom is None:
        flash("That classroom could not be found.", "error")
        return redirect(url_for("admin_dashboard"))

    student = fetch_one("SELECT id, name FROM users WHERE id = ? AND role = 'student'", (student_id,))
    if student is None:
        flash("That learner could not be found.", "error")
        return redirect(url_for("admin_dashboard"))

    cursor = get_db().execute(
        "DELETE FROM enrollments WHERE classroom_id = ? AND user_id = ?",
        (int(classroom["id"]), student_id),
    )
    get_db().commit()
    if cursor.rowcount:
        flash(f"{student['name']} removed from {classroom['title']}.", "success")
    else:
        flash("That learner is not enrolled in this classroom.", "error")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/classrooms/<slug>/assignments/create", methods=["POST"])
@login_required(("admin", "mentor"))
def admin_create_assignment(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    assignment_type = normalize_assignment_type(request.form.get("assignment_type", "challenge"))
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    resource_link = request.form.get("resource_link", "").strip()
    due_at = request.form.get("due_at", "").strip()

    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))
    if not title or not description or not due_at:
        flash("Assignment title, description, and due date are required.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    if resource_link and not is_valid_url(resource_link):
        flash("Paste a valid coursework resource link.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    description = append_resource_link(description, resource_link)
    due_value = normalize_due_at_input(due_at)

    try:
        get_db().execute(
            """
            INSERT INTO assignments (classroom_id, assignment_type, title, description, due_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(classroom["id"]), assignment_type, title, description, due_value, timestamp_now()),
        )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        flash("An assignment with that title already exists for this classroom.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    try:
        due_label = format_iso_label(due_value)
        notified_count = send_classroom_learner_notification_email(
            classroom_id=int(classroom["id"]),
            classroom_title=classroom["title"],
            item_title=title,
            item_type=assignment_type_label(assignment_type),
            message_body=f"Due: {due_label}\n\n{description}",
            action_url=url_for("lms_classroom", slug=slug, _external=True),
        )
        if notified_count:
            flash(f"Learner notification sent to {notified_count} enrolled learner(s).", "success")
    except RuntimeError as exc:
        flash(str(exc), "error")
    flash("Coursework created successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/admin/classrooms/<slug>/assignments/<int:assignment_id>/update", methods=["POST"])
@login_required(("mentor", "admin"))
def admin_update_assignment(slug: str, assignment_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    due_at = request.form.get("due_at", "").strip()
    assignment_type = normalize_assignment_type(request.form.get("assignment_type", "assignment"))

    if not title or not description or not due_at:
        flash("Assignment title, description, and due date are required.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    assignment = fetch_one(
        "SELECT id, assignment_type, due_at FROM assignments WHERE id = ? AND classroom_id = ?",
        (assignment_id, int(classroom["id"])),
    )
    if assignment is None:
        flash("That coursework item could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    due_value = normalize_due_at_input(due_at)
    try:
        get_db().execute(
            """
            UPDATE assignments
            SET assignment_type = ?, title = ?, description = ?, due_at = ?, challenge_reminder_sent = ?
            WHERE id = ?
            """,
            (
                assignment_type,
                title,
                description,
                due_value,
                0 if assignment_type == "challenge" else 1,
                assignment_id,
            ),
        )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        flash("Another coursework item already uses that title in this classroom.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    flash("Coursework updated successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/admin/classrooms/<slug>/assignments/<int:assignment_id>/delete", methods=["POST"])
@login_required(("mentor", "admin"))
def admin_delete_assignment(slug: str, assignment_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    assignment = fetch_one(
        "SELECT id FROM assignments WHERE id = ? AND classroom_id = ?",
        (assignment_id, int(classroom["id"])),
    )
    if assignment is None:
        flash("That coursework item could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute("DELETE FROM submissions WHERE assignment_id = ?", (assignment_id,))
    get_db().execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
    get_db().commit()
    flash("Coursework deleted successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/modules/create", methods=["POST"])
@login_required(("mentor", "admin"))
def create_module(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    summary = request.form.get("summary", "").strip()
    status = request.form.get("status", "").strip() or "In Progress"
    raw_items = request.form.get("items", "").strip()
    resource_link = request.form.get("resource_link", "").strip()
    if not title or not summary:
        flash("Module title and summary are required.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    if resource_link and not is_valid_url(resource_link):
        flash("Paste a valid module resource link.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    if resource_link:
        raw_items = append_resource_link(raw_items, resource_link, "Module resource")

    next_position_row = get_db().execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM modules WHERE classroom_id = ?",
        (int(classroom["id"]),),
    ).fetchone()
    next_position = int(next_position_row[0])

    try:
        cursor = get_db().execute(
            """
            INSERT INTO modules (classroom_id, title, status, summary, position)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(classroom["id"]), title, status, summary, next_position),
        )
        module_id = int(cursor.lastrowid)
        items = [line.strip() for line in raw_items.splitlines() if line.strip()]
        for index, item in enumerate(items, start=1):
            get_db().execute(
                """
                INSERT INTO module_items (module_id, content, position)
                VALUES (?, ?, ?)
                """,
                (module_id, item, index),
            )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        flash("A module with that title already exists in this classroom.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    flash("Coursework module created successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/modules/<int:module_id>/update", methods=["POST"])
@login_required(("mentor", "admin"))
def update_module(slug: str, module_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    summary = request.form.get("summary", "").strip()
    status = request.form.get("status", "").strip() or "In Progress"
    raw_items = request.form.get("items", "").strip()
    if not title or not summary:
        flash("Module title and summary are required.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    module = fetch_one(
        "SELECT id FROM modules WHERE id = ? AND classroom_id = ?",
        (module_id, int(classroom["id"])),
    )
    if module is None:
        flash("That module could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute(
        "UPDATE modules SET title = ?, status = ?, summary = ? WHERE id = ?",
        (title, status, summary, module_id),
    )
    get_db().execute("DELETE FROM module_items WHERE module_id = ?", (module_id,))
    items = [line.strip() for line in raw_items.splitlines() if line.strip()]
    for position, item in enumerate(items, start=1):
        get_db().execute(
            "INSERT INTO module_items (module_id, content, position) VALUES (?, ?, ?)",
            (module_id, item, position),
        )
    get_db().commit()
    flash("Coursework module updated successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/modules/<int:module_id>/delete", methods=["POST"])
@login_required(("mentor", "admin"))
def delete_module(slug: str, module_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    module = fetch_one(
        "SELECT id FROM modules WHERE id = ? AND classroom_id = ?",
        (module_id, int(classroom["id"])),
    )
    if module is None:
        flash("That module could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute("DELETE FROM module_items WHERE module_id = ?", (module_id,))
    get_db().execute("DELETE FROM modules WHERE id = ?", (module_id,))
    get_db().commit()
    flash("Coursework module deleted successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/attendance/<int:session_id>/update", methods=["POST"])
@login_required(("mentor", "admin"))
def update_attendance_session(slug: str, session_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    session_date = request.form.get("session_date", "").strip()
    if not title or not session_date:
        flash("Attendance session title and date are required.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    session_row = fetch_one(
        "SELECT id FROM attendance_sessions WHERE id = ? AND classroom_id = ?",
        (session_id, int(classroom["id"])),
    )
    if session_row is None:
        flash("That attendance session could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute(
        "UPDATE attendance_sessions SET title = ?, session_date = ? WHERE id = ?",
        (title, session_date, session_id),
    )
    get_db().commit()
    flash("Attendance session updated successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/attendance/<int:session_id>/delete", methods=["POST"])
@login_required(("mentor", "admin"))
def delete_attendance_session(slug: str, session_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    session_row = fetch_one(
        "SELECT id FROM attendance_sessions WHERE id = ? AND classroom_id = ?",
        (session_id, int(classroom["id"])),
    )
    if session_row is None:
        flash("That attendance session could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute("DELETE FROM attendance_records WHERE session_id = ?", (session_id,))
    get_db().execute("DELETE FROM attendance_sessions WHERE id = ?", (session_id,))
    get_db().commit()
    flash("Attendance session deleted successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/meetings/create", methods=["POST"])
@login_required(("mentor", "admin"))
def create_classroom_meeting(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    topic = request.form.get("topic", "").strip()
    agenda = request.form.get("agenda", "").strip()
    start_time_raw = request.form.get("start_time", "").strip()
    duration = request.form.get("duration", "").strip()

    if not topic or not start_time_raw or not duration:
        flash("Please provide topic, start time, and duration.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    try:
        duration_mins = int(duration)
        if duration_mins <= 0:
            raise ValueError()
    except ValueError:
        flash("Duration must be a positive integer.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    # Normalize start time string with +01:00 WAT offset, just like coursework due dates
    start_time_normalized = normalize_due_at_input(start_time_raw)

    # Call Zoho Meeting API client to schedule
    meeting_info = zoho_meeting.create_meeting(
        topic=topic,
        agenda=agenda,
        start_time_iso=start_time_raw, # Pass raw format to client which formats it for Zoho
        duration_mins=duration_mins
    )

    if not meeting_info:
        flash("Failed to schedule Zoho Meeting. Check your API credentials and try again.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    join_link = meeting_info["joinLink"]
    start_link = meeting_info["startLink"]
    if join_link.startswith("/"):
        join_link = request.url_root.rstrip("/") + join_link
    if start_link.startswith("/"):
        start_link = request.url_root.rstrip("/") + start_link

    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO live_sessions (
                classroom_id, meeting_key, topic, agenda, start_time, duration, join_link, start_link, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(classroom["id"]),
                meeting_info["meetingKey"],
                topic,
                agenda or None,
                start_time_normalized,
                duration_mins,
                join_link,
                start_link,
                timestamp_now()
            )
        )

        # Post an announcement automatically to the classroom stream
        announcement_title = f"Live Training Scheduled: {topic}"
        start_dt = datetime.fromisoformat(start_time_normalized)
        formatted_start = start_dt.strftime("%b %d, %Y at %I:%M %p WAT")
        announcement_body = (
            f"A new live training session has been scheduled!\n\n"
            f"Topic: {topic}\n"
            f"Date & Time: {formatted_start}\n"
            f"Duration: {duration_mins} minutes\n"
            f"Agenda: {agenda or 'No agenda provided.'}\n\n"
            f"Learners can join the session directly from the Live Training tab in the classroom portal."
        )

        db.execute(
            """
            INSERT INTO announcements (classroom_id, kind, title, body, posted_at)
            VALUES (?, 'announcement', ?, ?, ?)
            """,
            (int(classroom["id"]), announcement_title, announcement_body, timestamp_now())
        )
        db.commit()

        # Send learner notification email asynchronously/in try-except block
        try:
            send_classroom_learner_notification_email(
                classroom_id=int(classroom["id"]),
                classroom_title=classroom["title"],
                item_title=announcement_title,
                item_type="announcement",
                message_body=announcement_body,
                action_url=url_for("lms_classroom", slug=slug, _external=True)
            )
        except Exception as exc:
            app.logger.error(f"Failed to send classroom learner notification email: {exc}")

        flash("Live training session scheduled successfully and announced to class.", "success")
    except DB_INTEGRITY_ERRORS:
        flash("A meeting with that key already exists.", "error")
    except Exception as exc:
        app.logger.exception("Error scheduling classroom meeting")
        flash("An unexpected error occurred while scheduling the live session.", "error")

    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/meetings/<int:session_id>/delete", methods=["POST"])
@login_required(("mentor", "admin"))
def delete_classroom_meeting(slug: str, session_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    session_row = fetch_one(
        "SELECT id, meeting_key FROM live_sessions WHERE id = ? AND classroom_id = ?",
        (session_id, int(classroom["id"]))
    )
    if session_row is None:
        flash("That live training session could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    # Delete on Zoho first
    success = zoho_meeting.delete_meeting(session_row["meeting_key"])
    if not success:
        flash("Failed to delete meeting on Zoho. It may have already been deleted.", "warning")

    db = get_db()
    db.execute("DELETE FROM live_sessions WHERE id = ?", (session_id,))
    db.commit()

    flash("Live training session deleted successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/meetings/sync", methods=["POST"])
@login_required()
def sync_classroom_meetings(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found or is not assigned to you.", "error")
        return redirect(url_for("dashboard"))

    # Find completed meetings that are still pending recording
    pending_sessions = fetch_all(
        """
        SELECT * FROM live_sessions
        WHERE classroom_id = ? AND recording_status = 'pending'
        """,
        (int(classroom["id"]),)
    )

    synced_count = 0
    now_val = datetime.now(tz=UTC)
    db = get_db()

    for session in pending_sessions:
        try:
            start_dt = datetime.fromisoformat(session["start_time"])
            duration_delta = timedelta(minutes=int(session["duration"]))
            end_dt = start_dt + duration_delta

            if now_val > end_dt:
                # This session is completed, check for recording
                download_url, play_url = zoho_meeting.fetch_meeting_recording(session["meeting_key"])
                if download_url or play_url:
                    db.execute(
                        """
                        UPDATE live_sessions
                        SET recording_download_url = ?, recording_play_url = ?, recording_status = 'available'
                        WHERE id = ?
                        """,
                        (download_url, play_url, int(session["id"]))
                    )

                    # Auto-add to materials
                    rec_title = f"Recording: {session['topic']}"
                    existing_material = db.execute(
                        "SELECT id FROM materials WHERE classroom_id = ? AND title = ?",
                        (int(classroom["id"]), rec_title)
                    ).fetchone()

                    if not existing_material:
                        desc = f"Automatically imported recording from live training session on {format_iso_label(session['start_time'])}."
                        db.execute(
                            """
                            INSERT INTO materials (
                                classroom_id, uploader_id, title, description, material_url, stored_name, original_name, relative_path, uploaded_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                int(classroom["id"]),
                                int(classroom["mentor_id"]),
                                rec_title,
                                desc,
                                play_url or download_url,
                                "zoho-recording",
                                f"recording-{session['meeting_key']}",
                                play_url or download_url,
                                timestamp_now()
                            )
                        )
                    synced_count += 1
        except Exception as e:
            app.logger.error(f"Error manually syncing recording for meeting {session['meeting_key']}: {e}")

    if synced_count > 0:
        db.commit()
        flash(f"Synced {synced_count} new recording(s) from Zoho.", "success")
    else:
        flash("No new recordings found on Zoho. They may still be processing.", "info")

    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/meetings/<int:session_id>/recording", methods=["POST"])
@login_required(("mentor", "admin"))
def manual_add_recording(slug: str, session_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found.", "error")
        return redirect(url_for("dashboard"))

    session_row = fetch_one(
        "SELECT * FROM live_sessions WHERE id = ? AND classroom_id = ?",
        (session_id, int(classroom["id"]))
    )
    if not session_row:
        flash("Live session not found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    recording_url = request.form.get("recording_url", "").strip()
    if not recording_url:
        flash("Recording URL is required.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    db = get_db()
    db.execute(
        """
        UPDATE live_sessions
        SET recording_download_url = ?, recording_play_url = ?, recording_status = 'available'
        WHERE id = ?
        """,
        (recording_url, recording_url, session_id)
    )

    # Insert or update in materials table
    rec_title = f"Recording: {session_row['topic']}"
    existing_material = db.execute(
        "SELECT id FROM materials WHERE classroom_id = ? AND title = ?",
        (int(classroom["id"]), rec_title)
    ).fetchone()

    if not existing_material:
        desc = f"Manually added recording from live training session on {format_iso_label(session_row['start_time'])}."
        db.execute(
            """
            INSERT INTO materials (
                classroom_id, uploader_id, title, description, material_url, stored_name, original_name, relative_path, uploaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(classroom["id"]),
                int(g.user["id"]),
                rec_title,
                desc,
                recording_url,
                "zoho-recording",
                f"recording-{session_row['meeting_key']}",
                recording_url,
                timestamp_now()
            )
        )
    else:
        db.execute(
            "UPDATE materials SET material_url = ?, relative_path = ? WHERE id = ?",
            (recording_url, recording_url, int(existing_material["id"]))
        )

    db.commit()
    flash("Recording added manually and published to Course Materials.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/meetings/<int:session_id>/recording/delete", methods=["POST"])
@login_required(("mentor", "admin"))
def manual_delete_recording(slug: str, session_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("That classroom could not be found.", "error")
        return redirect(url_for("dashboard"))

    session_row = fetch_one(
        "SELECT * FROM live_sessions WHERE id = ? AND classroom_id = ?",
        (session_id, int(classroom["id"]))
    )
    if not session_row:
        flash("Live session not found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    db = get_db()
    db.execute(
        """
        UPDATE live_sessions
        SET recording_download_url = NULL, recording_play_url = NULL, recording_status = 'pending'
        WHERE id = ?
        """,
        (session_id,)
    )

    # Delete from materials table
    rec_title = f"Recording: {session_row['topic']}"
    db.execute(
        "DELETE FROM materials WHERE classroom_id = ? AND title = ?",
        (int(classroom["id"]), rec_title)
    )

    db.commit()
    flash("Recording link removed successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/join", methods=["POST"])
@login_required("student")
def join_classroom():
    code = request.form.get("code", "").strip().upper()
    if not code:
        flash("Enter a classroom code to join.", "error")
        return redirect(url_for("student_dashboard"))

    classroom = fetch_one("SELECT id, slug, title FROM classrooms WHERE code = ?", (code,))
    if classroom is None:
        flash("That classroom code was not found.", "error")
        return redirect(url_for("student_dashboard"))

    get_db().execute(
        "INSERT OR IGNORE INTO enrollments (classroom_id, user_id, created_at) VALUES (?, ?, ?)",
        (int(classroom["id"]), int(g.user["id"]), timestamp_now()),
    )
    get_db().commit()
    flash(f"You have joined {classroom['title']}.", "success")
    return redirect(url_for("lms_classroom", slug=classroom["slug"]))


@app.route("/lms/classroom/<slug>")
@login_required()
def lms_classroom(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom yet.", "error")
        return redirect(url_for("dashboard"))

    return render_template(
        "lms_classroom.html",
        page_title=f"{classroom['title']} | Tektutors Learning Hub",
        meta_description=f"Inside {classroom['title']}: class stream, assignments, modules, and learner progress.",
        active_page="lms",
        classroom=classroom,
    )


@app.route("/lms/meeting/mock/<meeting_key>")
@login_required()
def mock_meeting(meeting_key: str):
    db = get_db()
    meeting = db.execute(
        """
        SELECT s.*, c.title AS classroom_title, c.slug AS classroom_slug
        FROM live_sessions s
        JOIN classrooms c ON c.id = s.classroom_id
        WHERE s.meeting_key = ?
        """,
        (meeting_key,)
    ).fetchone()

    if not meeting:
        flash("This meeting does not exist.", "error")
        return redirect(url_for("dashboard"))

    classroom = get_classroom_for_user(meeting["classroom_slug"], g.user)
    if classroom is None:
        flash("You do not have permission to join this session.", "error")
        return redirect(url_for("dashboard"))

    role = request.args.get("role", "student")
    return render_template(
        "mock_meeting.html",
        meeting=meeting,
        role=role,
        classroom=classroom,
        page_title=f"Live Session: {meeting['topic']}",
        active_page="lms"
    )


@app.route("/lms/classroom/<slug>/materials/upload", methods=["POST"])
@login_required(("mentor", "admin"))
def upload_material(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    material_url = request.form.get("material_url", "").strip()

    if not material_url or not is_valid_url(material_url):
        flash("Paste a valid course material link.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    if not title:
        title = urlparse(material_url).netloc or "Course material"

    get_db().execute(
        """
        INSERT INTO materials (
            classroom_id, uploader_id, title, description, material_url, stored_name, original_name, relative_path, uploaded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(classroom["id"]),
            int(g.user["id"]),
            title,
            description,
            material_url,
            "external-link",
            material_url,
            material_url,
            timestamp_now(),
        ),
    )
    get_db().commit()
    try:
        notified_count = send_classroom_learner_notification_email(
            classroom_id=int(classroom["id"]),
            classroom_title=classroom["title"],
            item_title=title,
            item_type="resource",
            message_body=f"Material link: {material_url}\n\n{description}" if description else f"Material link: {material_url}",
            action_url=url_for("lms_classroom", slug=slug, _external=True),
        )
        if notified_count:
            flash(f"Learner notification sent to {notified_count} enrolled learner(s).", "success")
    except RuntimeError as exc:
        flash(str(exc), "error")
    flash("Course material link added successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/materials/<int:material_id>/update", methods=["POST"])
@login_required(("mentor", "admin"))
def update_material(slug: str, material_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    material_url = request.form.get("material_url", "").strip()
    if not title:
        flash("Material title is required.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    if material_url and not is_valid_url(material_url):
        flash("Paste a valid course material link.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    material = fetch_one(
        "SELECT id FROM materials WHERE id = ? AND classroom_id = ?",
        (material_id, int(classroom["id"])),
    )
    if material is None:
        flash("That course material could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    if material_url:
        get_db().execute(
            """
            UPDATE materials
            SET title = ?, description = ?, material_url = ?, original_name = ?, relative_path = ?
            WHERE id = ?
            """,
            (title, description, material_url, material_url, material_url, material_id),
        )
    else:
        get_db().execute(
            "UPDATE materials SET title = ?, description = ? WHERE id = ?",
            (title, description, material_id),
        )
    get_db().commit()
    flash("Course material updated successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/materials/<int:material_id>/delete", methods=["POST"])
@login_required(("mentor", "admin"))
def delete_material(slug: str, material_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    material = fetch_one(
        "SELECT id, material_url, relative_path FROM materials WHERE id = ? AND classroom_id = ?",
        (material_id, int(classroom["id"])),
    )
    if material is None:
        flash("That course material could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    if not material.get("material_url"):
        delete_uploaded_file(material["relative_path"])
    get_db().execute("DELETE FROM materials WHERE id = ?", (material_id,))
    get_db().commit()
    flash("Course material deleted successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/announcements/<int:announcement_id>/update", methods=["POST"])
@login_required(("mentor", "admin"))
def update_announcement(slug: str, announcement_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    resource_link = request.form.get("resource_link", "").strip()
    kind = normalize_class_post_kind(request.form.get("kind", "announcement"))
    if not title or not body:
        flash("Please provide both a title and message for the class post.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    if resource_link and not is_valid_url(resource_link):
        flash("Paste a valid class post link.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    body = append_resource_link(body, resource_link, "Shared link")

    announcement = fetch_one(
        "SELECT id FROM announcements WHERE id = ? AND classroom_id = ?",
        (announcement_id, int(classroom["id"])),
    )
    if announcement is None:
        flash("That class post could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    try:
        get_db().execute(
            "UPDATE announcements SET kind = ?, title = ?, body = ? WHERE id = ?",
            (kind, title, body, announcement_id),
        )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        flash("Another class post already uses that type and title.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    flash("Class post updated successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/announcements/<int:announcement_id>/delete", methods=["POST"])
@login_required(("mentor", "admin"))
def delete_announcement(slug: str, announcement_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    announcement = fetch_one(
        "SELECT id FROM announcements WHERE id = ? AND classroom_id = ?",
        (announcement_id, int(classroom["id"])),
    )
    if announcement is None:
        flash("That class post could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    get_db().commit()
    flash("Class post deleted successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/materials/<int:material_id>/download")
@login_required()
def download_material(material_id: int):
    material = fetch_one(
        """
        SELECT m.*, c.slug, c.mentor_id
        FROM materials m
        JOIN classrooms c ON c.id = m.classroom_id
        WHERE m.id = ?
        """,
        (material_id,),
    )
    if material is None:
        flash("That course material could not be found.", "error")
        return redirect(url_for("dashboard"))

    if g.user["role"] == "mentor":
        if int(material["mentor_id"]) != int(g.user["id"]):
            flash("You do not have access to that material.", "error")
            return redirect(url_for("dashboard"))
    elif g.user["role"] == "student":
        enrollment = fetch_one(
            "SELECT id FROM enrollments WHERE classroom_id = ? AND user_id = ?",
            (int(material["classroom_id"]), int(g.user["id"])),
        )
        if enrollment is None:
            flash("You do not have access to that material.", "error")
            return redirect(url_for("dashboard"))

    if material.get("material_url"):
        return redirect(material["material_url"])

    file_path = UPLOADS_DIR / material["relative_path"]
    if not file_path.exists():
        flash("The file is no longer available on disk.", "error")
        return redirect(url_for("lms_classroom", slug=material["slug"]))

    return send_from_directory(file_path.parent, file_path.name, as_attachment=True, download_name=material["original_name"])


@app.route("/lms/classroom/<slug>/announcement", methods=["POST"])
@login_required(("mentor", "admin"))
def post_announcement(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    kind = normalize_class_post_kind(request.form.get("kind", "announcement"))
    if not title or not body:
        flash("Please provide both a title and message for the class post.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    try:
        get_db().execute(
            """
            INSERT INTO announcements (classroom_id, kind, title, body, posted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(classroom["id"]), kind, title, body, timestamp_now()),
        )
        get_db().commit()
    except DB_INTEGRITY_ERRORS:
        flash("A class post with that type and title already exists.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    try:
        notified_count = send_classroom_learner_notification_email(
            classroom_id=int(classroom["id"]),
            classroom_title=classroom["title"],
            item_title=title,
            item_type=kind,
            message_body=body,
            action_url=url_for("lms_classroom", slug=slug, _external=True),
        )
        if notified_count:
            flash(f"Learner notification sent to {notified_count} enrolled learner(s).", "success")
    except RuntimeError as exc:
        flash(str(exc), "error")
    flash("Your classroom post is live.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/attendance", methods=["POST"])
@login_required(("mentor", "admin"))
def save_attendance(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    title = request.form.get("title", "").strip() or "Class session"
    session_date = request.form.get("session_date", "").strip()
    if not session_date:
        flash("Please choose an attendance date.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    learner_ids = [int(learner["id"]) for learner in classroom["learners"]]
    if not learner_ids:
        flash("There are no learners enrolled in this classroom yet.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    try:
        cursor = get_db().execute(
            """
            INSERT INTO attendance_sessions (classroom_id, title, session_date, created_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(classroom["id"]), title, session_date, int(g.user["id"]), timestamp_now()),
        )
        session_id = int(cursor.lastrowid)
    except DB_INTEGRITY_ERRORS:
        flash("An attendance session with that title and date already exists.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    for learner_id in learner_ids:
        status = normalize_attendance_status(request.form.get(f"attendance_status_{learner_id}", "present"))
        notes = request.form.get(f"attendance_notes_{learner_id}", "").strip()
        get_db().execute(
            """
            INSERT INTO attendance_records (session_id, student_id, status, notes, marked_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, learner_id, status, notes or None, timestamp_now()),
        )

    get_db().commit()
    flash("Attendance captured successfully.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/assignments/<int:assignment_id>/submit", methods=["POST"])
@login_required("student")
def submit_assignment(slug: str, assignment_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("student_dashboard"))

    assignment = fetch_one(
        "SELECT id, title, description, assignment_type FROM assignments WHERE id = ? AND classroom_id = ?",
        (assignment_id, int(classroom["id"])),
    )
    if assignment is None:
        flash("That assignment could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    progress = attendance_progress_for_student(
        int(classroom["id"]),
        int(g.user["id"]),
        attendance_target_from_duration(classroom.get("course_duration"), int(classroom["attendance_target"] or 8)),
    )
    if is_project_coursework(assignment) and int(progress["attendance_progress"]) < PROJECT_UNLOCK_PROGRESS:
        flash(f"Your project becomes active when your class progress reaches {PROJECT_UNLOCK_PROGRESS}%.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    existing_submission = fetch_one(
        """
        SELECT id, status, grade, feedback, COALESCE(resubmission_allowed, 0) AS resubmission_allowed
        FROM submissions
        WHERE assignment_id = ? AND student_id = ?
        """,
        (assignment_id, int(g.user["id"])),
    )
    if submission_locked(existing_submission):
        flash("This assignment has already been graded. You can only resubmit if your mentor allows it.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    content = request.form.get("content", "").strip()
    if not content or not is_valid_url(content):
        flash("Paste a valid submission link.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute(
        """
        INSERT INTO submissions (
            assignment_id, student_id, content, status, submitted_at, stored_name, original_name, relative_path, grade, feedback, resubmission_allowed
        )
        VALUES (?, ?, ?, 'submitted', ?, ?, ?, ?, NULL, NULL, 0)
        ON CONFLICT(assignment_id, student_id) DO UPDATE SET
            content = excluded.content,
            status = 'submitted',
            submitted_at = excluded.submitted_at,
            stored_name = NULL,
            original_name = NULL,
            relative_path = NULL,
            grade = NULL,
            feedback = NULL,
            resubmission_allowed = 0
        """,
        (
            assignment_id,
            int(g.user["id"]),
            content,
            timestamp_now(),
            None,
            None,
            None,
        ),
    )
    get_db().commit()
    try:
        send_submission_notification_email(
            mentor_name=classroom["mentor_name"],
            mentor_email=classroom["mentor_email"],
            learner_name=g.user["name"],
            classroom_title=classroom["title"],
            coursework_title=assignment["title"],
            coursework_type=assignment["assignment_type"],
            submission_url=content,
            review_url=url_for("lms_classroom", slug=slug, _external=True),
        )
    except RuntimeError as exc:
        flash(str(exc), "error")
    flash(f"Submitted: {assignment['title']}.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/projects/suggest", methods=["POST"])
@login_required("student")
def suggest_project(slug: str):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("student_dashboard"))

    title = request.form.get("project_title", "").strip()
    description = request.form.get("project_description", "").strip()
    if len(title) < 5:
        flash("Enter a clear project title.", "error")
        return redirect(url_for("lms_classroom", slug=slug))
    if len(description) < 20:
        flash("Describe your project idea in a little more detail.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute(
        """
        INSERT INTO project_suggestions (classroom_id, student_id, title, description, submitted_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(classroom["id"]), int(g.user["id"]), title, description, timestamp_now()),
    )
    get_db().commit()
    try:
        send_project_suggestion_email(
            mentor_name=classroom["mentor_name"],
            mentor_email=classroom["mentor_email"],
            learner_name=g.user["name"],
            classroom_title=classroom["title"],
            project_title=title,
            project_description=description,
            review_url=url_for("lms_classroom", slug=slug, _external=True),
        )
    except RuntimeError as exc:
        app.logger.warning("Project suggestion was saved but notification email failed: %s", exc)
    flash("Your project suggestion has been sent to your mentor.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/lms/classroom/<slug>/projects/<int:suggestion_id>/review", methods=["POST"])
@login_required(("admin", "mentor"))
def review_project_suggestion(slug: str, suggestion_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    status = request.form.get("status", "").strip().lower()
    mentor_note = request.form.get("mentor_note", "").strip()

    if status not in ("approved", "declined"):
        flash("Invalid project suggestion status.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    suggestion = fetch_one(
        """
        SELECT ps.*, u.name AS student_name, u.email AS student_email
        FROM project_suggestions ps
        JOIN users u ON u.id = ps.student_id
        WHERE ps.id = ? AND ps.classroom_id = ?
        """,
        (suggestion_id, int(classroom["id"])),
    )

    if not suggestion:
        flash("Project suggestion not found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    get_db().execute(
        """
        UPDATE project_suggestions
        SET status = ?, mentor_note = ?
        WHERE id = ?
        """,
        (status, mentor_note or None, suggestion_id),
    )
    get_db().commit()

    try:
        classroom_url = url_for("lms_classroom", slug=slug, _external=True)
        send_project_suggestion_review_email(
            student_name=suggestion["student_name"],
            student_email=suggestion["student_email"],
            classroom_title=classroom["title"],
            project_title=suggestion["title"],
            status=status,
            mentor_note=mentor_note,
            classroom_url=classroom_url,
        )
        flash(f"Project suggestion status updated to {status} and notification sent to learner.", "success")
    except RuntimeError as exc:
        app.logger.warning("Project suggestion was updated but notification email failed: %s", exc)
        flash(f"Project suggestion status updated to {status}, but learner notification email failed.", "warning")

    return redirect(url_for("lms_classroom", slug=slug))



@app.route("/lms/submissions/<int:submission_id>/download")
@login_required()
def download_submission_file(submission_id: int):
    submission = fetch_one(
        """
        SELECT
            s.*,
            a.classroom_id,
            c.slug,
            c.mentor_id
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        JOIN classrooms c ON c.id = a.classroom_id
        WHERE s.id = ?
        """,
        (submission_id,),
    )
    if submission is None or not submission.get("relative_path"):
        flash("That submission file could not be found.", "error")
        return redirect(url_for("dashboard"))

    allowed = False
    if g.user["role"] == "admin":
        allowed = True
    if g.user["role"] == "mentor" and int(submission["mentor_id"]) == int(g.user["id"]):
        allowed = True
    if g.user["role"] == "student" and int(submission["student_id"]) == int(g.user["id"]):
        allowed = True
    if not allowed:
        flash("You do not have access to that submission file.", "error")
        return redirect(url_for("dashboard"))

    file_path = UPLOADS_DIR / submission["relative_path"]
    if not file_path.exists():
        flash("The file is no longer available on disk.", "error")
        return redirect(url_for("lms_classroom", slug=submission["slug"]))

    return send_from_directory(file_path.parent, file_path.name, as_attachment=True, download_name=submission["original_name"])


@app.route("/lms/classroom/<slug>/submissions/<int:submission_id>/feedback", methods=["POST"])
@login_required(("mentor", "admin"))
def save_submission_feedback(slug: str, submission_id: int):
    classroom = get_classroom_for_user(slug, g.user)
    if classroom is None:
        flash("You do not have access to that classroom.", "error")
        return redirect(url_for("dashboard"))

    submission = fetch_one(
        """
        SELECT s.id
        FROM submissions s
        JOIN assignments a ON a.id = s.assignment_id
        WHERE s.id = ? AND a.classroom_id = ?
        """,
        (submission_id, int(classroom["id"])),
    )
    if submission is None:
        flash("That learner submission could not be found.", "error")
        return redirect(url_for("lms_classroom", slug=slug))

    grade = request.form.get("grade", "").strip()
    feedback = request.form.get("feedback", "").strip()
    status = request.form.get("status", "reviewed").strip().lower() or "reviewed"
    resubmission_allowed = 1 if request.form.get("resubmission_allowed") == "1" else 0

    get_db().execute(
        """
        UPDATE submissions
        SET grade = ?, feedback = ?, status = ?, resubmission_allowed = ?
        WHERE id = ?
        """,
        (grade or None, feedback or None, status, resubmission_allowed, submission_id),
    )
    get_db().commit()
    flash("Feedback saved for the learner submission.", "success")
    return redirect(url_for("lms_classroom", slug=slug))


@app.route("/careers", methods=["GET", "POST"])
def careers():
    if request.method == "POST":
        if public_form_blocked("careers", limit=4, min_seconds=5):
            return redirect(url_for("careers") + "#careers-form")

        name = request.form.get("name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        phone = request.form.get("phone", "").strip()
        training_area = request.form.get("training_area", "").strip()
        years_experience = request.form.get("years_experience", "").strip()
        availability = request.form.get("availability", "").strip()
        portfolio_url = request.form.get("portfolio_url", "").strip()
        message = request.form.get("message", "").strip()
        cv_file = request.files.get("cv")

        if not name or not email or not phone or not training_area or not years_experience or not availability:
            flash("Please complete your name, email, phone number, training area, experience, and availability.", "error")
            return redirect(url_for("careers") + "#careers-form")
        if not is_valid_email(email):
            flash("Please provide a valid email address.", "error")
            return redirect(url_for("careers") + "#careers-form")
        if too_many_links(portfolio_url, message, max_links=2):
            flash("Please remove extra links from your careers application.", "error")
            return redirect(url_for("careers") + "#careers-form")
        if cv_file is None or not cv_file.filename:
            flash("Please attach your CV before submitting your trainer application.", "error")
            return redirect(url_for("careers") + "#careers-form")

        try:
            cv_info = store_uploaded_file(
                cv_file,
                CAREERS_CVS_DIR,
                "trainer_cv",
                allowed_extensions=ALLOWED_CV_EXTENSIONS,
            )
            append_csv_row(
                CAREERS_FILE,
                [
                    timestamp_now(),
                    name,
                    email,
                    phone,
                    training_area,
                    years_experience,
                    availability,
                    portfolio_url,
                    message,
                    cv_info["original_name"],
                    cv_info["relative_path"],
                    request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    request.headers.get("User-Agent", "")[:200],
                ],
            )

            send_form_notification(
                subject=f"New Trainer Application: {name}",
                body=(
                    f"Name: {name}\n"
                    f"Email: {email}\n"
                    f"Phone: {phone}\n"
                    f"Training area: {training_area}\n"
                    f"Years of experience: {years_experience}\n"
                    f"Availability: {availability}\n"
                    f"Portfolio/link: {portfolio_url or 'Not provided'}\n"
                    f"CV file: {cv_info['original_name']}\n"
                    f"CV path: {cv_info['relative_path']}\n\n"
                    f"Message:\n{message or 'No message provided.'}"
                ),
            )
        except Exception as exc:
            app.logger.error(f"Error saving careers application: {exc}")
            flash("We could not submit your trainer application right now. Please try again shortly.", "error")
            return redirect(url_for("careers") + "#careers-form")

        flash("Your trainer application has been submitted. We will review your CV and contact you if there is a fit.", "success")
        return redirect(url_for("careers") + "#careers-form")

    return render_template(
        "careers.html",
        page_title="Careers | Join Tektutors as a Trainer",
        meta_description="Apply to join the Tektutors team as a trainer and share your CV for review.",
        active_page="careers",
        programs=PROGRAMS,
    )


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        if public_form_blocked("contact", limit=5):
            return redirect(url_for("contact"))

        name = request.form.get("name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        phone = request.form.get("phone", "").strip()
        program = request.form.get("program", "").strip()
        preferred_date = request.form.get("preferred_date", "").strip()
        preferred_time = request.form.get("preferred_time", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not preferred_date or not preferred_time or not message:
            flash("Please complete your name, email, preferred consultation date and time, and message so we can help you quickly.", "error")
            return redirect(url_for("contact") + "#contact-form")
        if not is_valid_email(email):
            flash("Please provide a valid email address.", "error")
            return redirect(url_for("contact") + "#contact-form")
        if too_many_links(message, max_links=1):
            flash("Please remove extra links from your consultation request.", "error")
            return redirect(url_for("contact") + "#contact-form")

        try:
            append_csv_row(
                CONTACT_FILE,
                [
                    timestamp_now(),
                    name,
                    email,
                    phone,
                    program,
                    "",
                    preferred_date,
                    preferred_time,
                    message,
                    request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                    request.headers.get("User-Agent", "")[:200],
                ],
            )

            send_form_notification(
                subject=f"New Tektutors Consultation Request: {name}",
                body=(
                    f"Name: {name}\n"
                    f"Email: {email}\n"
                    f"Phone: {phone}\n"
                    f"Program: {program}\n"
                    f"Preferred consultation date: {preferred_date}\n"
                    f"Preferred consultation time: {preferred_time}\n"
                    f"Goal:\n{message}"
                ),
            )
        except Exception as exc:
            app.logger.error(f"Error saving contact form application: {exc}")
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
    destination = request.form.get("next_page", "index").strip()
    allowed_destinations = {"index", "about", "programs", "contact", "careers", "lms"}
    if destination not in allowed_destinations:
        destination = "index"

    if public_form_blocked("newsletter", limit=6, min_seconds=2):
        return redirect(url_for(destination) + "#newsletter")

    email = normalize_email(request.form.get("email", ""))
    interest = request.form.get("interest", "").strip()

    if not email or not is_valid_email(email):
        flash("Please enter a valid email to subscribe.", "error")
        return redirect(url_for(destination) + "#newsletter")
    if len(interest) > 120 or too_many_links(interest, max_links=0):
        flash("Please choose a shorter newsletter interest without links.", "error")
        return redirect(url_for(destination) + "#newsletter")

    try:
        append_csv_row(
            NEWSLETTER_FILE,
            [
                timestamp_now(),
                email,
                interest,
                request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            ],
        )
        send_form_notification(
            subject=f"New Newsletter Subscription: {email}",
            body=f"New subscription received.\n\nEmail: {email}\nInterest: {interest}",
        )
    except Exception as exc:
        app.logger.error(f"Failed to save newsletter subscription: {exc}")
        flash("Subscription failed. Please try again shortly.", "error")
        return redirect(url_for(destination) + "#newsletter")

    flash("You are in. Expect practical insights and program updates in your inbox.", "success")
    return redirect(url_for(destination) + "#newsletter")


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "environment": APP_ENV}), 200


@app.get("/maintenance")
def maintenance():
    response = make_response(render_template("maintenance.html"), 503)
    response.headers["Retry-After"] = str(app.config["MAINTENANCE_RETRY_AFTER"])
    return response


def should_send_meeting_reminder(start_time: str, now_value: datetime | None = None) -> bool:
    start_dt = datetime.fromisoformat(start_time)
    now_dt = now_value or datetime.now(tz=UTC)
    time_until_start = start_dt - now_dt
    return timedelta(seconds=0) <= time_until_start <= MEETING_REMINDER_LEAD_TIME


def send_meeting_reminder_email(meeting: dict[str, Any]) -> int:
    classroom_id = int(meeting["classroom_id"])
    classroom_title = meeting["classroom_title"]
    classroom_slug = meeting["classroom_slug"]
    topic = meeting["topic"]
    agenda = meeting["agenda"]
    start_time = meeting["start_time"]
    duration = meeting["duration"]
    join_link = meeting["join_link"]
    start_link = meeting["start_link"]

    # 1. Fetch learners
    learners = fetch_all(
        """
        SELECT name, email
        FROM users u
        JOIN enrollments e ON e.user_id = u.id
        WHERE
            e.classroom_id = ?
            AND u.role = 'student'
            AND u.is_approved = 1
            AND COALESCE(u.is_suspended, 0) = 0
        ORDER BY u.name ASC
        """,
        (classroom_id,),
    )
    learners = [learner for learner in learners if is_valid_email(learner["email"])]

    # 2. Fetch mentor details
    mentor = fetch_one(
        """
        SELECT u.name, u.email
        FROM users u
        JOIN classrooms c ON c.mentor_id = u.id
        WHERE c.id = ?
        """,
        (classroom_id,),
    )
    if mentor and not is_valid_email(mentor["email"]):
        mentor = None

    if not learners and mentor is None:
        app.logger.warning("No valid reminder recipients found for meeting %s", meeting["id"])
        return 0

    ensure_mail_delivery_configured()

    subject = f"Reminder: Live Session '{topic}' in {classroom_title} starts in 30 minutes"
    start_dt = datetime.fromisoformat(start_time)
    formatted_start = start_dt.strftime("%b %d, %Y at %I:%M %p WAT")
    sent_count = 0

    try:
        with mail.connect() as connection:
            # Send to learners
            for learner in learners:
                body = (
                    f"Hello {learner['name']},\n\n"
                    f"This is a reminder that the live training session for {classroom_title} starts in 30 minutes!\n\n"
                    f"Topic: {topic}\n"
                    f"Date & Time: {formatted_start}\n"
                    f"Duration: {duration} minutes\n"
                    f"Agenda: {agenda or 'No agenda provided.'}\n\n"
                    f"You can join the session directly using this link:\n"
                    f"{join_link}\n\n"
                    f"Or log into the portal to join: {url_for('lms_classroom', slug=classroom_slug, _external=True)}\n\n"
                    "Best regards,\n"
                    "Tektutors Learning Hub"
                )
                message = Message(
                    subject=subject,
                    recipients=[learner["email"]],
                    body=body,
                    sender=app.config["MAIL_DEFAULT_SENDER"],
                )
                connection.send(message)
                sent_count += 1

            # Send to mentor
            if mentor:
                mentor_body = (
                    f"Hello Mentor {mentor['name']},\n\n"
                    f"This is a reminder that the live training session you are hosting for {classroom_title} starts in 30 minutes!\n\n"
                    f"Topic: {topic}\n"
                    f"Date & Time: {formatted_start}\n"
                    f"Duration: {duration} minutes\n"
                    f"Agenda: {agenda or 'No agenda provided.'}\n\n"
                    f"Use this link to start/host the meeting:\n"
                    f"{start_link}\n\n"
                    f"Learning Hub link: {url_for('lms_classroom', slug=classroom_slug, _external=True)}\n\n"
                    "Best regards,\n"
                    "Tektutors Learning Hub"
                )
                mentor_message = Message(
                    subject=subject,
                    recipients=[mentor["email"]],
                    body=mentor_body,
                    sender=app.config["MAIL_DEFAULT_SENDER"],
                )
                connection.send(mentor_message)
                sent_count += 1

    except Exception as exc:
        app.logger.exception("Failed to send meeting reminder emails for session %s", meeting["id"])
        raise RuntimeError("Failed to send meeting reminder emails.") from exc

    return sent_count


def should_send_challenge_due_reminder(due_at: str, now_value: datetime | None = None) -> bool:
    due_dt = datetime.fromisoformat(due_at)
    now_dt = now_value or datetime.now(tz=UTC)
    time_until_due = due_dt - now_dt
    return timedelta(seconds=0) <= time_until_due <= CHALLENGE_DUE_REMINDER_LEAD_TIME


def send_challenge_due_reminder_email(challenge: dict[str, Any]) -> int:
    assignment_id = int(challenge["id"])
    classroom_id = int(challenge["classroom_id"])
    classroom_title = challenge["classroom_title"]
    classroom_slug = challenge["classroom_slug"]
    challenge_title = challenge["title"]
    due_at = challenge["due_at"]

    learners = fetch_all(
        """
        SELECT u.name, u.email
        FROM enrollments e
        JOIN users u ON u.id = e.user_id
        LEFT JOIN submissions s ON s.assignment_id = ? AND s.student_id = u.id
        WHERE
            e.classroom_id = ?
            AND u.role = 'student'
            AND u.is_approved = 1
            AND COALESCE(u.is_suspended, 0) = 0
            AND s.id IS NULL
        ORDER BY u.name ASC
        """,
        (assignment_id, classroom_id),
    )
    learners = [learner for learner in learners if is_valid_email(learner["email"])]
    if not learners:
        app.logger.info("No pending learner recipients for challenge reminder %s", assignment_id)
        return 0

    ensure_mail_delivery_configured()

    due_dt = datetime.fromisoformat(due_at)
    formatted_due = due_dt.strftime("%b %d, %Y at %I:%M %p WAT")
    classroom_url = url_for("lms_classroom", slug=classroom_slug, _external=True)
    subject = f"Reminder: Challenge submission due in 24 hours - {challenge_title}"
    sent_count = 0

    try:
        with mail.connect() as connection:
            for learner in learners:
                body = (
                    f"Hello {learner['name']},\n\n"
                    f"This is a reminder that your challenge submission for {classroom_title} is due in 24 hours.\n\n"
                    f"Challenge: {challenge_title}\n"
                    f"Due: {formatted_due}\n\n"
                    f"Open your classroom in the Learning Hub to submit your work:\n"
                    f"{classroom_url}\n\n"
                    "Best regards,\n"
                    "Tektutors Learning Hub"
                )
                message = Message(
                    subject=subject,
                    recipients=[learner["email"]],
                    body=body,
                    sender=app.config["MAIL_DEFAULT_SENDER"],
                )
                connection.send(message)
                sent_count += 1
    except Exception as exc:
        app.logger.exception("Failed to send challenge due reminder emails for assignment %s", assignment_id)
        raise RuntimeError("Failed to send challenge due reminder emails.") from exc

    return sent_count


def check_and_send_challenge_due_reminders() -> None:
    with app.app_context():
        db = get_db()
        rows = db.execute(
            """
            SELECT a.*, c.title AS classroom_title, c.slug AS classroom_slug
            FROM assignments a
            JOIN classrooms c ON c.id = a.classroom_id
            WHERE
                a.assignment_type = 'challenge'
                AND COALESCE(a.challenge_reminder_sent, 0) = 0
            """
        ).fetchall()

        if not rows:
            return

        for row in rows:
            try:
                challenge_dict = dict(row)
                if should_send_challenge_due_reminder(challenge_dict["due_at"]):
                    sent_count = send_challenge_due_reminder_email(challenge_dict)
                    db.execute(
                        "UPDATE assignments SET challenge_reminder_sent = 1 WHERE id = ?",
                        (int(challenge_dict["id"]),),
                    )
                    db.commit()
                    app.logger.info(
                        "Challenge due reminder processed for %s recipient(s): %s (ID: %s)",
                        sent_count,
                        challenge_dict["title"],
                        challenge_dict["id"],
                    )
            except Exception as e:
                app.logger.exception(f"Error checking/sending challenge reminder for assignment {row['id']}: {e}")


def check_and_send_meeting_reminders() -> None:
    with app.app_context():
        db = get_db()
        rows = db.execute(
            """
            SELECT s.*, c.title AS classroom_title, c.slug AS classroom_slug
            FROM live_sessions s
            JOIN classrooms c ON c.id = s.classroom_id
            WHERE COALESCE(s.reminder_sent, 0) = 0
            """
        ).fetchall()

        if not rows:
            return

        for row in rows:
            try:
                meeting_dict = dict(row)
                rewrite_meeting_links_if_mock(meeting_dict)
                if should_send_meeting_reminder(meeting_dict["start_time"]):
                    sent_count = send_meeting_reminder_email(meeting_dict)

                    if sent_count > 0:
                        db.execute(
                            "UPDATE live_sessions SET reminder_sent = 1 WHERE id = ?",
                            (int(meeting_dict["id"]),)
                        )
                        db.commit()
                        app.logger.info(
                            "Reminder sent to %s recipient(s) for meeting: %s (ID: %s)",
                            sent_count,
                            meeting_dict["topic"],
                            meeting_dict["id"],
                        )
            except Exception as e:
                app.logger.exception(f"Error checking/sending reminder for meeting {row['id']}: {e}")


def sync_all_pending_recordings() -> int:
    with app.app_context():
        db = get_db()
        pending_sessions = db.execute(
            """
            SELECT s.*, c.mentor_id
            FROM live_sessions s
            JOIN classrooms c ON c.id = s.classroom_id
            WHERE s.recording_status = 'pending'
            """
        ).fetchall()

        synced_count = 0
        now_val = datetime.now(tz=UTC)

        for session in pending_sessions:
            try:
                session_dict = dict(session)
                start_dt = datetime.fromisoformat(session_dict["start_time"])
                duration_delta = timedelta(minutes=int(session_dict["duration"]))
                end_dt = start_dt + duration_delta

                if now_val > end_dt:
                    download_url, play_url = zoho_meeting.fetch_meeting_recording(session_dict["meeting_key"])
                    if download_url or play_url:
                        db.execute(
                            """
                            UPDATE live_sessions
                            SET recording_download_url = ?, recording_play_url = ?, recording_status = 'available'
                            WHERE id = ?
                            """,
                            (download_url, play_url, int(session_dict["id"]))
                        )

                        rec_title = f"Recording: {session_dict['topic']}"
                        existing_material = db.execute(
                            "SELECT id FROM materials WHERE classroom_id = ? AND title = ?",
                            (int(session_dict["classroom_id"]), rec_title)
                        ).fetchone()

                        if not existing_material:
                            desc = f"Automatically imported recording from live training session on {format_iso_label(session_dict['start_time'])}."
                            db.execute(
                                """
                                INSERT INTO materials (
                                    classroom_id, uploader_id, title, description, material_url, stored_name, original_name, relative_path, uploaded_at
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    int(session_dict["classroom_id"]),
                                    int(session_dict["mentor_id"]),
                                    rec_title,
                                    desc,
                                    play_url or download_url,
                                    "zoho-recording",
                                    f"recording-{session_dict['meeting_key']}",
                                    play_url or download_url,
                                    timestamp_now()
                                )
                            )
                        synced_count += 1
            except Exception as e:
                app.logger.exception(f"Error auto-syncing recording for meeting {session.get('meeting_key') if isinstance(session, dict) else session['meeting_key']}: {e}")

        if synced_count > 0:
            db.commit()
            app.logger.info(f"Auto-synced {synced_count} recording(s) from Zoho.")
        return synced_count


def start_reminder_scheduler() -> None:
    global _meeting_reminder_scheduler_started
    if _meeting_reminder_scheduler_started:
        return
    _meeting_reminder_scheduler_started = True

    def run_scheduler():
        app.logger.info("Meeting reminder background scheduler started.")
        while True:
            try:
                time.sleep(MEETING_REMINDER_CHECK_INTERVAL_SECONDS)
                check_and_send_meeting_reminders()
                check_and_send_challenge_due_reminders()
                sync_all_pending_recordings()
            except Exception as e:
                app.logger.error(f"Error in meeting reminder scheduler loop: {e}")
                time.sleep(10)

    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()


if not IS_PRODUCTION and os.environ.get("SKIP_DB_INIT", "false").lower() != "true":
    initialize_storage()

if ENABLE_BACKGROUND_SCHEDULER and os.environ.get("SKIP_DB_INIT", "false").lower() != "true":
    if not IS_PRODUCTION or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_reminder_scheduler()


@app.cli.command("init-db")
def init_db_command() -> None:
    """Initialize the database storage, run migrations, and seed data."""
    import click
    click.echo("Initializing database storage...")
    initialize_storage()
    click.echo("Database storage initialized successfully.")


@app.cli.command("run-scheduler")
def run_scheduler_command() -> None:
    """Run meeting/challenge reminders and recording sync checks immediately."""
    import click
    click.echo("Running reminder scheduler checks...")
    check_and_send_meeting_reminders()
    check_and_send_challenge_due_reminders()
    click.echo("Running live session recording sync checks...")
    sync_all_pending_recordings()
    click.echo("All scheduler tasks completed.")


@app.route("/test_certificate")
def test_certificate():
    """Render a certificate with hard‑coded data for debugging.
    This route is safe to call in development; it does not require a logged‑in user.
    """
    # Hard‑coded demo values
    demo_name = "Demo Student"
    demo_classroom = {"title": "Demo Course", "slug": "demo-course"}
    demo_courses = ["Demo Course"]
    issued_label = datetime.now(tz=UTC).strftime("%B %d, %Y")
    certificate_id = "TT-0000-0000"
    verify_url = url_for("verify_certificate", certificate_id=certificate_id, _external=True)
    html = render_template(
        "certificate.html",
        learner_name=demo_name,
        classroom=demo_classroom,
        courses=demo_courses,
        issued_label=issued_label,
        certificate_id=certificate_id,
        verify_url=verify_url,
        logo_url=url_for("static", filename="images/tektutors-logo.svg", _external=True),
        qr_url=f"https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={verify_url}",
        template_url=url_for("static", filename="images/tektutors-certificate-template.png", _external=True),
    )
    return html


# ==============================================================================
# TEKTUTORS GROWTH ASSOCIATE PORTAL & CRM LOGIC
# ==============================================================================

PROPOSALS_DIR = UPLOADS_DIR / "proposals"

ADMIN_ROLES = ("admin", "growth_manager", "corporate_staff", "finance")

def generate_referral_id() -> str:
    year = datetime.now(tz=UTC).strftime("%Y")
    count_row = fetch_one("SELECT COUNT(*) as c FROM prospects")
    next_num = (count_row["c"] if count_row else 0) + 1
    return f"TT-GA-{year}-{next_num:05d}"


def check_prospect_duplicate(email: str, phone: str, organization: str = "", full_name: str = "") -> dict[str, Any] | None:
    norm_email = normalize_email(email) if email else ""
    if norm_email:
        p_row = fetch_one("SELECT id, referral_id, status FROM prospects WHERE LOWER(email) = ?", (norm_email,))
        if p_row:
            return {"type": "Email Match", "referral_id": p_row.get("referral_id"), "id": p_row["id"]}
        u_row = fetch_one("SELECT id, email FROM users WHERE LOWER(email) = ?", (norm_email,))
        if u_row:
            return {"type": "Existing TekTutors Account Email Match", "referral_id": None, "id": u_row["id"]}

    clean_phone = re.sub(r"\D", "", phone or "")
    if clean_phone and len(clean_phone) >= 7:
        rows = fetch_all("SELECT id, referral_id, phone_number, status FROM prospects")
        for r in rows:
            r_phone = re.sub(r"\D", "", r.get("phone_number") or "")
            if r_phone and (r_phone == clean_phone or (len(clean_phone) >= 10 and clean_phone[-10:] == r_phone[-10:])):
                return {"type": "Phone Number Match", "referral_id": r.get("referral_id"), "id": r["id"]}

    if organization and full_name:
        o_clean = organization.strip().lower()
        n_clean = full_name.strip().lower()
        p_row = fetch_one(
            "SELECT id, referral_id, status FROM prospects WHERE LOWER(organization) = ? AND LOWER(full_name) = ?",
            (o_clean, n_clean),
        )
        if p_row:
            return {"type": "Organization & Contact Name Match", "referral_id": p_row.get("referral_id"), "id": p_row["id"]}

    return None


def create_audit_log(user_id: int | None, entity_type: str, entity_id: int, action: str, previous_value: str = "", new_value: str = "") -> None:
    try:
        db = get_db()
        ip = client_ip_address() if request else "system"
        now_str = timestamp_now()
        db.execute(
            """
            INSERT INTO audit_logs (user_id, entity_type, entity_id, action, previous_value, new_value, ip_address, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, entity_type, entity_id, action, str(previous_value or ""), str(new_value or ""), ip, now_str),
        )
    except Exception as exc:
        app.logger.warning("Failed to record audit log: %s", exc)


def get_growth_associate_profile(user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT u.id as user_id, u.name, u.email, u.is_approved, u.is_suspended, u.created_at,
               g.phone_number, g.whatsapp_number, g.location, g.country, g.communication_channel,
               g.current_occupation, g.company_name, g.linkedin_url, g.network_areas, g.access_industries,
               g.monthly_prospect_reach, g.agreed_to_terms, g.status as profile_status,
               g.bank_name, g.account_number, g.account_name, g.payout_notes
        FROM users u
        LEFT JOIN growth_associate_profiles g ON g.user_id = u.id
        WHERE u.id = ?
        """,
        (user_id,),
    )


def get_growth_associate_kpis(user_id: int | None = None) -> dict[str, Any]:
    params = (user_id,) if user_id else ()
    where_clause = "WHERE growth_associate_id = ?" if user_id else ""
    where_prefix = "WHERE" if not user_id else "AND"

    total_prospects = fetch_one(f"SELECT COUNT(*) as c FROM prospects {where_clause}", params)["c"]
    new_prospects = fetch_one(f"SELECT COUNT(*) as c FROM prospects {where_clause} {where_prefix} status = 'Submitted'", params)["c"]
    in_followup = fetch_one(f"SELECT COUNT(*) as c FROM prospects {where_clause} {where_prefix} status IN ('Contacted', 'Follow-up Requested')", params)["c"]
    converted = fetch_one(f"SELECT COUNT(*) as c FROM prospects {where_clause} {where_prefix} status IN ('Converted', 'Commission Pending', 'Commission Approved', 'Commission Paid')", params)["c"]

    comm_where = "WHERE growth_associate_id = ?" if user_id else ""
    comm_prefix = "WHERE" if not user_id else "AND"

    pending_comm = fetch_one(f"SELECT COALESCE(SUM(commission_amount), 0) as s FROM commissions {comm_where} {comm_prefix} commission_status IN ('Pending', 'Under Review')", params)["s"]
    approved_comm = fetch_one(f"SELECT COALESCE(SUM(commission_amount), 0) as s FROM commissions {comm_where} {comm_prefix} commission_status = 'Approved'", params)["s"]
    paid_comm = fetch_one(f"SELECT COALESCE(SUM(commission_amount), 0) as s FROM commissions {comm_where} {comm_prefix} commission_status = 'Paid'", params)["s"]

    return {
        "total_prospects": total_prospects,
        "new_prospects": new_prospects,
        "in_followup": in_followup,
        "converted": converted,
        "pending_commissions": pending_comm,
        "approved_commissions": approved_comm,
        "paid_commissions": paid_comm,
    }


# --- PUBLIC & AUTH ROUTES ---

@app.route("/growth-associate")
def growth_associate_landing():
    return render_template(
        "growth_associate_landing.html",
        page_title="Growth Associate Program | TekTutors",
        meta_description="Connect individuals and organizations to personalized data training opportunities and earn commission as a TekTutors Growth Associate.",
        active_page="growth_associate",
    )


@app.route("/growth-associate/register", methods=["GET", "POST"])
def growth_associate_register():
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        if public_form_rate_limited("ga_register", limit=5):
            flash("Too many registration attempts. Please try again shortly.", "error")
            return redirect(url_for("growth_associate_register"))

        name = request.form.get("name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        phone_number = request.form.get("phone_number", "").strip()
        whatsapp_number = request.form.get("whatsapp_number", "").strip()
        location = request.form.get("location", "").strip()
        country = request.form.get("country", "Nigeria").strip()
        communication_channel = request.form.get("communication_channel", "Email").strip()

        current_occupation = request.form.get("current_occupation", "").strip()
        company_name = request.form.get("company_name", "").strip()
        linkedin_url = request.form.get("linkedin_url", "").strip()
        network_areas = request.form.get("network_areas", "").strip()
        access_industries = request.form.get("access_industries", "").strip()
        monthly_prospect_reach = request.form.get("monthly_prospect_reach", "").strip()
        agreed_terms = request.form.get("agreed_to_terms")

        if not all([name, email, password, phone_number, location, current_occupation]):
            flash("Please fill in all required personal and professional fields.", "error")
            return redirect(url_for("growth_associate_register"))

        if not is_valid_email(email):
            flash("Please provide a valid email address.", "error")
            return redirect(url_for("growth_associate_register"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("growth_associate_register"))

        if len(password) < 8:
            flash("Password must be at least 8 characters long.", "error")
            return redirect(url_for("growth_associate_register"))

        if not agreed_terms:
            flash("You must agree to the TekTutors Growth Associate terms and commission guidelines.", "error")
            return redirect(url_for("growth_associate_register"))

        if get_user_by_email(email):
            flash("An account with that email address already exists.", "error")
            return redirect(url_for("growth_associate_register"))

        now_str = timestamp_now()
        pw_hash = generate_password_hash(password)
        db = get_db()

        cursor = db.execute(
            """
            INSERT INTO users (name, email, password_hash, role, is_approved, is_suspended, created_at)
            VALUES (?, ?, ?, 'growth_associate', 0, 0, ?)
            """,
            (name, email, pw_hash, now_str),
        )
        user_id = cursor.lastrowid

        db.execute(
            """
            INSERT INTO growth_associate_profiles (
                user_id, phone_number, whatsapp_number, location, country, communication_channel,
                current_occupation, company_name, linkedin_url, network_areas, access_industries,
                monthly_prospect_reach, agreed_to_terms, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'pending', ?, ?)
            """,
            (
                user_id, phone_number, whatsapp_number, location, country, communication_channel,
                current_occupation, company_name, linkedin_url, network_areas, access_industries,
                monthly_prospect_reach, now_str, now_str
            ),
        )
        db.commit()

        create_audit_log(user_id, "user", user_id, "Growth Associate Registered", "", "status=pending")

        send_form_notification(
            subject="New TekTutors Growth Associate Application",
            body=f"New Growth Associate registered:\nName: {name}\nEmail: {email}\nOccupation: {current_occupation}\nPhone: {phone_number}\n\nReview in Admin Panel.",
        )

        return render_template(
            "growth_associate_register.html",
            page_title="Application Submitted | TekTutors",
            submitted=True,
            associate_name=name,
            active_page="growth_associate",
        )

    return render_template(
        "growth_associate_register.html",
        page_title="Become a Growth Associate | TekTutors",
        submitted=False,
        active_page="growth_associate",
    )


@app.route("/growth-associate/login", methods=["GET", "POST"])
def growth_associate_login():
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        user = get_user_by_email(email)

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("growth_associate_login"))

        if user["role"] != "growth_associate" and user["role"] not in ADMIN_ROLES:
            flash("This portal is reserved for TekTutors Growth Associates.", "error")
            return redirect(url_for("growth_associate_login"))

        if not is_user_approved(user):
            flash("Thank you for your interest. Your Growth Associate application is under review by our admin team.", "info")
            return redirect(url_for("growth_associate_login"))

        if is_user_suspended(user):
            flash("Your account has been suspended. Please contact TekTutors support.", "error")
            return redirect(url_for("growth_associate_login"))

        login_user(user)
        flash(f"Welcome back, {user['name']}!", "success")
        return redirect(url_for("growth_associate_dashboard"))

    return render_template(
        "growth_associate_login.html",
        page_title="Growth Associate Login | TekTutors",
        active_page="growth_associate",
    )


# --- GROWTH ASSOCIATE PORTAL ROUTES ---

@app.route("/growth-associate/dashboard")
@login_required("growth_associate")
def growth_associate_dashboard():
    profile = get_growth_associate_profile(g.user["id"])
    kpis = get_growth_associate_kpis(g.user["id"])

    recent_referrals = fetch_all(
        """
        SELECT p.*, c.commission_status, c.commission_amount
        FROM prospects p
        LEFT JOIN commissions c ON c.prospect_id = p.id
        WHERE p.growth_associate_id = ?
        ORDER BY p.submitted_at DESC
        LIMIT 8
        """,
        (g.user["id"],),
    )

    return render_template(
        "growth_associate_dashboard.html",
        page_title="Growth Associate Dashboard | TekTutors",
        profile=profile,
        kpis=kpis,
        recent_referrals=recent_referrals,
        active_page="ga_dashboard",
        active_subpage="dashboard",
    )


@app.route("/growth-associate/prospects")
@login_required("growth_associate")
def growth_associate_prospects():
    search_q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    opp_filter = request.args.get("opp_type", "").strip()

    sql = """
        SELECT p.*, c.commission_status, c.commission_amount
        FROM prospects p
        LEFT JOIN commissions c ON c.prospect_id = p.id
        WHERE p.growth_associate_id = ?
    """
    params: list[Any] = [g.user["id"]]

    if search_q:
        sql += " AND (p.full_name LIKE ? OR p.email LIKE ? OR p.organization LIKE ? OR p.referral_id LIKE ?)"
        term = f"%{search_q}%"
        params.extend([term, term, term, term])

    if status_filter:
        sql += " AND p.status = ?"
        params.append(status_filter)

    if opp_filter:
        sql += " AND p.opportunity_type = ?"
        params.append(opp_filter)

    sql += " ORDER BY p.submitted_at DESC"
    prospects = fetch_all(sql, tuple(params))

    return render_template(
        "growth_associate_prospects.html",
        page_title="My Prospects & Referrals | TekTutors",
        prospects=prospects,
        search_q=search_q,
        status_filter=status_filter,
        opp_filter=opp_filter,
        active_page="ga_dashboard",
        active_subpage="prospects",
    )


@app.route("/growth-associate/prospects/new", methods=["GET", "POST"])
@login_required("growth_associate")
def growth_associate_prospect_new():
    duplicate_warning = None

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        whatsapp_number = request.form.get("whatsapp_number", "").strip()
        email = normalize_email(request.form.get("email", ""))
        location = request.form.get("location", "").strip()
        country = request.form.get("country", "Nigeria").strip()
        organization = request.form.get("organization", "").strip()
        job_title = request.form.get("job_title", "").strip()
        preferred_contact_method = request.form.get("preferred_contact_method", "Phone Call").strip()

        opportunity_type = request.form.get("opportunity_type", "Individual Training").strip()
        training_interests_list = request.form.getlist("training_interests")
        other_interest = request.form.get("other_training_interest", "").strip()
        if other_interest:
            training_interests_list.append(other_interest)
        training_interests = ", ".join(training_interests_list) if training_interests_list else "Not sure / Needs consultation"

        prospect_details = request.form.get("prospect_details", "").strip()
        lead_temperature = request.form.get("lead_temperature", "Warm").strip()

        try:
            estimated_value = float(request.form.get("estimated_value", 0) or 0)
        except ValueError:
            estimated_value = 0.0

        request_followup = bool(request.form.get("request_followup"))
        preferred_followup_method = request.form.get("preferred_followup_method", "Phone Call").strip()
        preferred_followup_time = request.form.get("preferred_followup_time", "Any time").strip()
        followup_instructions = request.form.get("followup_instructions", "").strip()

        if not all([full_name, phone_number, email]):
            flash("Full name, phone number, and email are required.", "error")
            return render_template(
                "growth_associate_prospect_new.html",
                page_title="Submit New Prospect | TekTutors",
                active_page="ga_dashboard",
                active_subpage="new_prospect",
            )

        # Check duplicate prospect protection
        dup_match = check_prospect_duplicate(email, phone_number, organization, full_name)
        override_duplicate = bool(request.form.get("override_duplicate"))

        if dup_match and not override_duplicate:
            duplicate_warning = {
                "match_type": dup_match["type"],
                "message": "This prospect may already exist in the TekTutors system. Please confirm before proceeding.",
            }
            return render_template(
                "growth_associate_prospect_new.html",
                page_title="Submit New Prospect | TekTutors",
                duplicate_warning=duplicate_warning,
                form_data=request.form,
                active_page="ga_dashboard",
                active_subpage="new_prospect",
            )

        ref_id = generate_referral_id()
        now_str = timestamp_now()
        is_dup = 1 if dup_match else 0
        dup_notes = f"Flagged duplicate match: {dup_match['type']}" if dup_match else ""

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO prospects (
                referral_id, growth_associate_id, full_name, phone_number, whatsapp_number, email,
                location, country, organization, job_title, preferred_contact_method, opportunity_type,
                training_interests, prospect_details, lead_temperature, estimated_value, status,
                is_duplicate, duplicate_notes, submitted_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Submitted', ?, ?, ?, ?)
            """,
            (
                ref_id, g.user["id"], full_name, phone_number, whatsapp_number, email,
                location, country, organization, job_title, preferred_contact_method, opportunity_type,
                training_interests, prospect_details, lead_temperature, estimated_value,
                is_dup, dup_notes, now_str, now_str
            ),
        )
        prospect_id = cursor.lastrowid

        # Insert commission entry
        db.execute(
            """
            INSERT INTO commissions (prospect_id, growth_associate_id, commission_amount, commission_status, payment_approval_status, created_at, updated_at)
            VALUES (?, ?, 0.0, 'Pending', 'Unapproved', ?, ?)
            """,
            (prospect_id, g.user["id"], now_str, now_str),
        )

        # Create follow-up request if selected
        if request_followup:
            db.execute(
                """
                INSERT INTO follow_up_requests (
                    prospect_id, growth_associate_id, preferred_contact_method, preferred_contact_time,
                    reason, additional_instructions, status, requested_at, updated_at
                ) VALUES (?, ?, ?, ?, 'Initial Follow-up Requested by Associate', ?, 'Requested', ?, ?)
                """,
                (prospect_id, g.user["id"], preferred_followup_method, preferred_followup_time, followup_instructions, now_str, now_str),
            )
            db.execute("UPDATE prospects SET status = 'Follow-up Requested' WHERE id = ?", (prospect_id,))

        db.commit()
        create_audit_log(g.user["id"], "prospect", prospect_id, "Prospect Submitted", "", f"referral_id={ref_id}")

        send_form_notification(
            subject=f"New Growth Referral Submitted [{ref_id}]",
            body=f"Referral ID: {ref_id}\nSubmitted by: {g.user['name']}\nProspect: {full_name} ({organization or 'Individual'})\nInterest: {training_interests}\nFollow-up requested: {'Yes' if request_followup else 'No'}",
        )

        flash(f"Prospect '{full_name}' submitted successfully with Referral ID: {ref_id}", "success")
        return redirect(url_for("growth_associate_prospect_detail", prospect_id=prospect_id))

    return render_template(
        "growth_associate_prospect_new.html",
        page_title="Submit New Prospect | TekTutors",
        active_page="ga_dashboard",
        active_subpage="new_prospect",
    )


@app.route("/growth-associate/prospects/<int:prospect_id>")
@login_required("growth_associate")
def growth_associate_prospect_detail(prospect_id: int):
    prospect = fetch_one(
        """
        SELECT p.*, c.commission_status, c.commission_amount, c.payment_approval_status, c.approved_at, c.paid_at
        FROM prospects p
        LEFT JOIN commissions c ON c.prospect_id = p.id
        WHERE p.id = ? AND p.growth_associate_id = ?
        """,
        (prospect_id, g.user["id"]),
    )

    if not prospect:
        flash("Referral not found or access denied.", "error")
        return redirect(url_for("growth_associate_prospects"))

    followups = fetch_all(
        "SELECT * FROM follow_up_requests WHERE prospect_id = ? ORDER BY requested_at DESC",
        (prospect_id,),
    )

    audits = fetch_all(
        "SELECT action, timestamp FROM audit_logs WHERE entity_type = 'prospect' AND entity_id = ? ORDER BY timestamp DESC",
        (prospect_id,),
    )

    return render_template(
        "growth_associate_prospect_detail.html",
        page_title=f"Referral {prospect['referral_id']} | TekTutors",
        prospect=prospect,
        followups=followups,
        audits=audits,
        active_page="ga_dashboard",
        active_subpage="prospects",
    )


@app.route("/growth-associate/follow-ups", methods=["GET", "POST"])
@login_required("growth_associate")
def growth_associate_followups():
    if request.method == "POST":
        prospect_id = request.form.get("prospect_id")
        method = request.form.get("preferred_contact_method", "Phone Call")
        time_slot = request.form.get("preferred_contact_time", "Any time")
        reason = request.form.get("reason", "").strip()
        instructions = request.form.get("additional_instructions", "").strip()

        if not prospect_id:
            flash("Please select a prospect for follow-up.", "error")
            return redirect(url_for("growth_associate_followups"))

        now_str = timestamp_now()
        db = get_db()
        db.execute(
            """
            INSERT INTO follow_up_requests (
                prospect_id, growth_associate_id, preferred_contact_method, preferred_contact_time,
                reason, additional_instructions, status, requested_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Requested', ?, ?)
            """,
            (prospect_id, g.user["id"], method, time_slot, reason, instructions, now_str, now_str),
        )
        db.execute("UPDATE prospects SET status = 'Follow-up Requested' WHERE id = ? AND growth_associate_id = ?", (prospect_id, g.user["id"]))
        db.commit()

        flash("Follow-up request submitted to TekTutors mentors.", "success")
        return redirect(url_for("growth_associate_followups"))

    followup_requests = fetch_all(
        """
        SELECT f.*, p.full_name as prospect_name, p.referral_id, p.organization, u.name as staff_name
        FROM follow_up_requests f
        JOIN prospects p ON p.id = f.prospect_id
        LEFT JOIN users u ON u.id = f.assigned_staff_id
        WHERE f.growth_associate_id = ?
        ORDER BY f.requested_at DESC
        """,
        (g.user["id"],),
    )

    my_prospects = fetch_all("SELECT id, referral_id, full_name, organization FROM prospects WHERE growth_associate_id = ? ORDER BY full_name ASC", (g.user["id"],))

    return render_template(
        "growth_associate_followups.html",
        page_title="Follow-up Requests | TekTutors",
        followup_requests=followup_requests,
        my_prospects=my_prospects,
        active_page="ga_dashboard",
        active_subpage="followups",
    )


@app.route("/growth-associate/corporate-opportunities", methods=["GET", "POST"])
@login_required("growth_associate")
def growth_associate_corporate():
    if request.method == "POST":
        org_name = request.form.get("organization_name", "").strip()
        org_type = request.form.get("organization_type", "Company").strip()
        website = request.form.get("website", "").strip()
        location = request.form.get("location", "").strip()

        contact_person = request.form.get("contact_person", "").strip()
        contact_position = request.form.get("contact_position", "").strip()
        contact_phone = request.form.get("contact_phone", "").strip()
        contact_email = normalize_email(request.form.get("contact_email", ""))

        training_topic = request.form.get("training_topic", "").strip()
        skills_required = request.form.get("skills_required", "").strip()
        try:
            participant_count = int(request.form.get("participant_count", 1) or 1)
        except ValueError:
            participant_count = 1
        training_level = request.form.get("training_level", "Intermediate").strip()
        delivery_mode = request.form.get("delivery_mode", "Online").strip()
        preferred_date = request.form.get("preferred_date", "").strip()
        expected_duration = request.form.get("expected_duration", "").strip()
        training_objectives = request.form.get("training_objectives", "").strip()
        additional_requirements = request.form.get("additional_requirements", "").strip()

        identification_source = request.form.get("identification_source", "").strip()
        expressed_interest = request.form.get("expressed_interest", "Yes").strip()
        meeting_taken = request.form.get("meeting_taken", "No").strip()
        existing_budget = request.form.get("existing_budget", "Under Discussion").strip()
        try:
            estimated_budget = float(request.form.get("estimated_budget", 0) or 0)
        except ValueError:
            estimated_budget = 0.0
        expected_decision_date = request.form.get("expected_decision_date", "").strip()
        request_proposal = bool(request.form.get("request_proposal"))

        if not all([org_name, contact_person, contact_phone, contact_email, training_topic]):
            flash("Please fill in organization name, contact details, and training topic.", "error")
            return redirect(url_for("growth_associate_corporate"))

        now_str = timestamp_now()
        db = get_db()

        cursor = db.execute(
            """
            INSERT INTO corporate_opportunities (
                growth_associate_id, organization_name, organization_type, website, location,
                contact_person, contact_position, contact_phone, contact_email, training_topic,
                skills_required, participant_count, training_level, delivery_mode, preferred_date,
                expected_duration, training_objectives, additional_requirements, identification_source,
                expressed_interest, meeting_taken, existing_budget, estimated_budget, expected_decision_date,
                proposal_requested, status, submitted_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Submitted', ?, ?)
            """,
            (
                g.user["id"], org_name, org_type, website, location,
                contact_person, contact_position, contact_phone, contact_email, training_topic,
                skills_required, participant_count, training_level, delivery_mode, preferred_date,
                expected_duration, training_objectives, additional_requirements, identification_source,
                expressed_interest, meeting_taken, existing_budget, estimated_budget, expected_decision_date,
                1 if request_proposal else 0, now_str, now_str
            ),
        )
        opp_id = cursor.lastrowid

        if request_proposal:
            db.execute(
                """
                INSERT INTO proposal_requests (corporate_opportunity_id, growth_associate_id, title, requirements, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'Requested', ?, ?)
                """,
                (opp_id, g.user["id"], f"Proposal for {org_name}", training_objectives or training_topic, now_str, now_str),
            )

        db.commit()
        create_audit_log(g.user["id"], "corporate_opportunity", opp_id, "Corporate Opportunity Submitted", "", f"org={org_name}")

        send_form_notification(
            subject=f"New Corporate Training Lead: {org_name}",
            body=f"Organization: {org_name}\nType: {org_type}\nContact: {contact_person} ({contact_email})\nParticipants: {participant_count}\nProposal Requested: {'Yes' if request_proposal else 'No'}",
        )

        flash("Corporate opportunity submitted successfully.", "success")
        return redirect(url_for("growth_associate_corporate"))

    opportunities = fetch_all(
        "SELECT * FROM corporate_opportunities WHERE growth_associate_id = ? ORDER BY submitted_at DESC",
        (g.user["id"],),
    )

    return render_template(
        "growth_associate_corporate.html",
        page_title="Corporate & Institutional Opportunities | TekTutors",
        opportunities=opportunities,
        active_page="ga_dashboard",
        active_subpage="corporate",
    )


@app.route("/growth-associate/proposals")
@login_required("growth_associate")
def growth_associate_proposals():
    proposal_items = fetch_all(
        """
        SELECT pr.id as request_id, pr.title, pr.status as request_status, pr.created_at,
               co.organization_name, p.id as proposal_id, p.stored_name, p.original_name, p.status as proposal_file_status
        FROM proposal_requests pr
        LEFT JOIN corporate_opportunities co ON co.id = pr.corporate_opportunity_id
        LEFT JOIN proposals p ON p.proposal_request_id = pr.id
        WHERE pr.growth_associate_id = ?
        ORDER BY pr.created_at DESC
        """,
        (g.user["id"],),
    )

    return render_template(
        "growth_associate_proposals.html",
        page_title="Corporate Proposals | TekTutors",
        proposal_items=proposal_items,
        active_page="ga_dashboard",
        active_subpage="proposals",
    )


@app.route("/growth-associate/proposals/<int:proposal_id>/download")
@login_required("growth_associate")
def growth_associate_proposal_download(proposal_id: int):
    prop = fetch_one(
        "SELECT * FROM proposals WHERE id = ? AND growth_associate_id = ?",
        (proposal_id, g.user["id"]),
    )

    if not prop:
        flash("Proposal document not found.", "error")
        return redirect(url_for("growth_associate_proposals"))

    file_path = PROPOSALS_DIR / prop["stored_name"]
    if not file_path.exists():
        flash("Proposal file does not exist on server.", "error")
        return redirect(url_for("growth_associate_proposals"))

    return send_from_directory(
        PROPOSALS_DIR,
        prop["stored_name"],
        as_attachment=True,
        download_name=prop["original_name"],
    )


@app.route("/growth-associate/payments")
@login_required("growth_associate")
def growth_associate_payments():
    commissions = fetch_all(
        """
        SELECT c.*, p.referral_id, p.full_name as prospect_name, p.organization, p.opportunity_type, p.submitted_at as ref_date
        FROM commissions c
        JOIN prospects p ON p.id = c.prospect_id
        WHERE c.growth_associate_id = ?
        ORDER BY c.created_at DESC
        """,
        (g.user["id"],),
    )

    profile = get_growth_associate_profile(g.user["id"])
    kpis = get_growth_associate_kpis(g.user["id"])

    return render_template(
        "growth_associate_payments.html",
        page_title="My Commissions & Payments | TekTutors",
        commissions=commissions,
        profile=profile,
        kpis=kpis,
        active_page="ga_dashboard",
        active_subpage="payments",
    )


@app.route("/growth-associate/profile", methods=["GET", "POST"])
@login_required("growth_associate")
def growth_associate_profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        whatsapp_number = request.form.get("whatsapp_number", "").strip()
        location = request.form.get("location", "").strip()
        current_occupation = request.form.get("current_occupation", "").strip()
        company_name = request.form.get("company_name", "").strip()
        linkedin_url = request.form.get("linkedin_url", "").strip()

        bank_name = request.form.get("bank_name", "").strip()
        account_number = request.form.get("account_number", "").strip()
        account_name = request.form.get("account_name", "").strip()
        payout_notes = request.form.get("payout_notes", "").strip()

        now_str = timestamp_now()
        db = get_db()

        db.execute("UPDATE users SET name = ? WHERE id = ?", (name, g.user["id"]))

        db.execute(
            """
            UPDATE growth_associate_profiles
            SET phone_number = ?, whatsapp_number = ?, location = ?, current_occupation = ?,
                company_name = ?, linkedin_url = ?, bank_name = ?, account_number = ?,
                account_name = ?, payout_notes = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (
                phone_number, whatsapp_number, location, current_occupation, company_name,
                linkedin_url, bank_name, account_number, account_name, payout_notes, now_str, g.user["id"]
            ),
        )
        db.commit()

        flash("Profile and payment information updated successfully.", "success")
        return redirect(url_for("growth_associate_profile"))

    profile = get_growth_associate_profile(g.user["id"])

    return render_template(
        "growth_associate_profile.html",
        page_title="My Growth Associate Profile | TekTutors",
        profile=profile,
        active_page="ga_dashboard",
        active_subpage="profile",
    )


@app.route("/growth-associate/help")
@login_required("growth_associate")
def growth_associate_help():
    return render_template(
        "growth_associate_help.html",
        page_title="Growth Associate Support & FAQ | TekTutors",
        active_page="ga_dashboard",
        active_subpage="help",
    )


# --- ADMIN GROWTH ASSOCIATE MANAGEMENT ROUTES ---

@app.route("/admin/growth-associates")
@login_required(ADMIN_ROLES)
def admin_growth_associates():
    status_filter = request.args.get("status", "").strip()
    sql = """
        SELECT u.id as user_id, u.name, u.email, u.is_approved, u.is_suspended, u.created_at,
               g.phone_number, g.location, g.current_occupation, g.status as profile_status
        FROM users u
        JOIN growth_associate_profiles g ON g.user_id = u.id
        WHERE u.role = 'growth_associate'
    """
    params: list[Any] = []
    if status_filter:
        sql += " AND g.status = ?"
        params.append(status_filter)

    sql += " ORDER BY u.created_at DESC"
    associates = fetch_all(sql, tuple(params))

    return render_template(
        "admin_growth_associates.html",
        page_title="Manage Growth Associates | TekTutors Admin",
        associates=associates,
        status_filter=status_filter,
        active_page="lms",
        active_subpage="admin_ga",
    )


@app.route("/admin/growth-associates/<int:user_id>/status", methods=["POST"])
@login_required(ADMIN_ROLES)
def admin_growth_associate_status(user_id: int):
    new_status = request.form.get("status", "").strip().lower()
    now_str = timestamp_now()
    db = get_db()

    user = get_user_by_id(user_id)
    if not user or user["role"] != "growth_associate":
        flash("Invalid Growth Associate account.", "error")
        return redirect(url_for("admin_growth_associates"))

    if new_status == "approve":
        db.execute("UPDATE users SET is_approved = 1, approved_at = ? WHERE id = ?", (now_str, user_id))
        db.execute("UPDATE growth_associate_profiles SET status = 'approved', updated_at = ? WHERE user_id = ?", (now_str, user_id))
        db.commit()
        create_audit_log(g.user["id"], "user", user_id, "Approve Associate", "pending", "approved")
        flash(f"Approved Growth Associate '{user['name']}'.", "success")

    elif new_status == "reject":
        db.execute("UPDATE users SET is_approved = 0 WHERE id = ?", (user_id,))
        db.execute("UPDATE growth_associate_profiles SET status = 'rejected', updated_at = ? WHERE user_id = ?", (now_str, user_id))
        db.commit()
        create_audit_log(g.user["id"], "user", user_id, "Reject Associate", "pending", "rejected")
        flash(f"Rejected Growth Associate application for '{user['name']}'.", "info")

    elif new_status == "suspend":
        db.execute("UPDATE users SET is_suspended = 1, suspended_at = ? WHERE id = ?", (now_str, user_id))
        db.execute("UPDATE growth_associate_profiles SET status = 'suspended', updated_at = ? WHERE user_id = ?", (now_str, user_id))
        db.commit()
        create_audit_log(g.user["id"], "user", user_id, "Suspend Associate", "approved", "suspended")
        flash(f"Suspended Growth Associate '{user['name']}'.", "warning")

    elif new_status == "reactivate":
        db.execute("UPDATE users SET is_suspended = 0, is_approved = 1 WHERE id = ?", (user_id,))
        db.execute("UPDATE growth_associate_profiles SET status = 'approved', updated_at = ? WHERE user_id = ?", (now_str, user_id))
        db.commit()
        create_audit_log(g.user["id"], "user", user_id, "Reactivate Associate", "suspended", "approved")
        flash(f"Reactivated Growth Associate '{user['name']}'.", "success")

    return redirect(url_for("admin_growth_associates"))


@app.route("/admin/referrals")
@login_required(ADMIN_ROLES)
def admin_referrals():
    status_filter = request.args.get("status", "").strip()
    search_q = request.args.get("q", "").strip()

    sql = """
        SELECT p.*, u.name as associate_name, u.email as associate_email,
               c.commission_amount, c.commission_status
        FROM prospects p
        JOIN users u ON u.id = p.growth_associate_id
        LEFT JOIN commissions c ON c.prospect_id = p.id
        WHERE 1=1
    """
    params: list[Any] = []

    if status_filter:
        sql += " AND p.status = ?"
        params.append(status_filter)

    if search_q:
        sql += " AND (p.full_name LIKE ? OR p.referral_id LIKE ? OR p.organization LIKE ? OR u.name LIKE ?)"
        term = f"%{search_q}%"
        params.extend([term, term, term, term])

    sql += " ORDER BY p.submitted_at DESC"
    prospects = fetch_all(sql, tuple(params))

    return render_template(
        "admin_referrals.html",
        page_title="Referral CRM Management | TekTutors Admin",
        prospects=prospects,
        status_filter=status_filter,
        search_q=search_q,
        active_page="lms",
        active_subpage="admin_referrals",
    )


@app.route("/admin/referrals/<int:prospect_id>/status", methods=["POST"])
@login_required(ADMIN_ROLES)
def admin_referral_status(prospect_id: int):
    new_status = request.form.get("status", "").strip()
    commission_amount = request.form.get("commission_amount")

    prospect = fetch_one("SELECT * FROM prospects WHERE id = ?", (prospect_id,))
    if not prospect:
        flash("Referral record not found.", "error")
        return redirect(url_for("admin_referrals"))

    prev_status = prospect["status"]
    now_str = timestamp_now()
    db = get_db()

    db.execute("UPDATE prospects SET status = ?, updated_at = ? WHERE id = ?", (new_status, now_str, prospect_id))

    if commission_amount is not None:
        try:
            amt = float(commission_amount)
            db.execute(
                """
                UPDATE commissions
                SET commission_amount = ?, updated_at = ?
                WHERE prospect_id = ?
                """,
                (amt, now_str, prospect_id),
            )
        except ValueError:
            pass

    if new_status == "Converted":
        db.execute(
            """
            UPDATE commissions
            SET commission_status = 'Under Review', updated_at = ?
            WHERE prospect_id = ?
            """,
            (now_str, prospect_id),
        )

    db.commit()
    create_audit_log(g.user["id"], "prospect", prospect_id, f"Status changed to '{new_status}'", prev_status, new_status)

    flash(f"Referral {prospect['referral_id']} status updated to '{new_status}'.", "success")
    return redirect(url_for("admin_referrals"))


@app.route("/admin/follow-ups", methods=["GET", "POST"])
@login_required(ADMIN_ROLES)
def admin_followups():
    if request.method == "POST":
        request_id = request.form.get("request_id")
        assigned_staff_id = request.form.get("assigned_staff_id")
        status = request.form.get("status", "In Progress").strip()
        notes = request.form.get("internal_notes", "").strip()

        if request_id:
            now_str = timestamp_now()
            db = get_db()
            db.execute(
                """
                UPDATE follow_up_requests
                SET assigned_staff_id = ?, status = ?, internal_notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (assigned_staff_id or None, status, notes, now_str, request_id),
            )
            db.commit()
            flash("Follow-up task updated successfully.", "success")
            return redirect(url_for("admin_followups"))

    followups = fetch_all(
        """
        SELECT f.*, p.full_name as prospect_name, p.phone_number as prospect_phone, p.email as prospect_email, p.referral_id,
               u.name as associate_name, s.name as staff_name
        FROM follow_up_requests f
        JOIN prospects p ON p.id = f.prospect_id
        JOIN users u ON u.id = f.growth_associate_id
        LEFT JOIN users s ON s.id = f.assigned_staff_id
        ORDER BY f.requested_at DESC
        """
    )

    staff_members = fetch_all("SELECT id, name, role FROM users WHERE role IN ('admin', 'mentor', 'growth_manager') ORDER BY name ASC")

    return render_template(
        "admin_followups.html",
        page_title="Manage Follow-up Tasks | TekTutors Admin",
        followups=followups,
        staff_members=staff_members,
        active_page="lms",
        active_subpage="admin_followups",
    )


@app.route("/admin/corporate-opportunities")
@login_required(ADMIN_ROLES)
def admin_corporate_opportunities():
    opportunities = fetch_all(
        """
        SELECT c.*, u.name as associate_name, u.email as associate_email
        FROM corporate_opportunities c
        JOIN users u ON u.id = c.growth_associate_id
        ORDER BY c.submitted_at DESC
        """
    )

    return render_template(
        "admin_corporate.html",
        page_title="Corporate Opportunities | TekTutors Admin",
        opportunities=opportunities,
        active_page="lms",
        active_subpage="admin_corporate",
    )


@app.route("/admin/proposals", methods=["GET", "POST"])
@login_required(ADMIN_ROLES)
def admin_proposals():
    if request.method == "POST":
        proposal_request_id = request.form.get("proposal_request_id")
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        file_obj = request.files.get("proposal_file")

        if not proposal_request_id or not file_obj or not file_obj.filename:
            flash("Please choose a proposal request and upload a document file.", "error")
            return redirect(url_for("admin_proposals"))

        req_row = fetch_one("SELECT * FROM proposal_requests WHERE id = ?", (proposal_request_id,))
        if not req_row:
            flash("Invalid proposal request ID.", "error")
            return redirect(url_for("admin_proposals"))

        upload_res = store_uploaded_file(file_obj, PROPOSALS_DIR, "proposal")
        now_str = timestamp_now()
        db = get_db()

        db.execute(
            """
            INSERT INTO proposals (proposal_request_id, growth_associate_id, title, description, stored_name, original_name, relative_path, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Ready', ?)
            """,
            (
                proposal_request_id, req_row["growth_associate_id"], title or req_row["title"],
                description, upload_res["stored_name"], upload_res["original_name"],
                upload_res["relative_path"], now_str
            ),
        )

        db.execute("UPDATE proposal_requests SET status = 'Ready', updated_at = ? WHERE id = ?", (now_str, proposal_request_id))
        db.commit()

        create_audit_log(g.user["id"], "proposal", proposal_request_id, "Proposal Uploaded", "", upload_res["original_name"])

        flash("Official training proposal document uploaded and notified to Growth Associate.", "success")
        return redirect(url_for("admin_proposals"))

    proposal_requests = fetch_all(
        """
        SELECT pr.*, u.name as associate_name, co.organization_name
        FROM proposal_requests pr
        JOIN users u ON u.id = pr.growth_associate_id
        LEFT JOIN corporate_opportunities co ON co.id = pr.corporate_opportunity_id
        ORDER BY pr.created_at DESC
        """
    )

    uploaded_proposals = fetch_all(
        """
        SELECT p.*, pr.title as request_title, u.name as associate_name
        FROM proposals p
        JOIN proposal_requests pr ON pr.id = p.proposal_request_id
        JOIN users u ON u.id = p.growth_associate_id
        ORDER BY p.created_at DESC
        """
    )

    return render_template(
        "admin_proposals.html",
        page_title="Corporate Proposals Management | TekTutors Admin",
        proposal_requests=proposal_requests,
        uploaded_proposals=uploaded_proposals,
        active_page="lms",
        active_subpage="admin_proposals",
    )


@app.route("/admin/commissions", methods=["GET", "POST"])
@login_required(ADMIN_ROLES)
def admin_commissions():
    if request.method == "POST":
        commission_id = request.form.get("commission_id")
        action = request.form.get("action", "").strip()
        amount_val = request.form.get("amount")
        notes = request.form.get("notes", "").strip()

        comm = fetch_one("SELECT * FROM commissions WHERE id = ?", (commission_id,))
        if not comm:
            flash("Commission record not found.", "error")
            return redirect(url_for("admin_commissions"))

        now_str = timestamp_now()
        db = get_db()

        if action == "approve":
            try:
                amt = float(amount_val) if amount_val else float(comm["commission_amount"])
            except ValueError:
                amt = float(comm["commission_amount"])

            db.execute(
                """
                UPDATE commissions
                SET commission_amount = ?, commission_status = 'Approved', payment_approval_status = 'Approved',
                    approved_by = ?, approved_at = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (amt, g.user["id"], now_str, notes, now_str, commission_id),
            )
            db.execute("UPDATE prospects SET status = 'Commission Approved' WHERE id = ?", (comm["prospect_id"],))
            db.commit()
            create_audit_log(g.user["id"], "commission", commission_id, "Commission Approved", f"amt={comm['commission_amount']}", f"amt={amt}")
            flash("Commission approved successfully.", "success")

        elif action == "mark_paid":
            db.execute(
                """
                UPDATE commissions
                SET commission_status = 'Paid', paid_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now_str, now_str, commission_id),
            )
            db.execute("UPDATE prospects SET status = 'Commission Paid' WHERE id = ?", (comm["prospect_id"],))
            db.commit()
            create_audit_log(g.user["id"], "commission", commission_id, "Commission Marked Paid", "Approved", "Paid")
            flash("Commission marked as Paid.", "success")

        return redirect(url_for("admin_commissions"))

    commissions = fetch_all(
        """
        SELECT c.*, p.referral_id, p.full_name as prospect_name, p.organization, p.status as prospect_status,
               u.name as associate_name, u.email as associate_email,
               g.bank_name, g.account_number, g.account_name
        FROM commissions c
        JOIN prospects p ON p.id = c.prospect_id
        JOIN users u ON u.id = c.growth_associate_id
        LEFT JOIN growth_associate_profiles g ON g.user_id = u.id
        ORDER BY c.created_at DESC
        """
    )

    kpis = get_growth_associate_kpis()

    return render_template(
        "admin_commissions.html",
        page_title="Commission Approvals & Payouts | TekTutors Admin",
        commissions=commissions,
        kpis=kpis,
        active_page="lms",
        active_subpage="admin_commissions",
    )


@app.route("/admin/growth-associates/export")
@login_required(ADMIN_ROLES)
def admin_export_reports():
    report_type = request.args.get("type", "referrals").strip()

    if report_type == "associates":
        rows = fetch_all(
            """
            SELECT u.name, u.email, g.phone_number, g.location, g.current_occupation, g.status, u.created_at
            FROM users u
            JOIN growth_associate_profiles g ON g.user_id = u.id
            WHERE u.role = 'growth_associate'
            ORDER BY u.created_at DESC
            """
        )
        fieldnames = ["name", "email", "phone_number", "location", "current_occupation", "status", "created_at"]
        filename = "growth_associates_report.csv"
    else:
        rows = fetch_all(
            """
            SELECT p.referral_id, p.full_name as prospect_name, p.phone_number, p.email, p.organization,
                   p.opportunity_type, p.training_interests, p.status, p.submitted_at, u.name as associate_name
            FROM prospects p
            JOIN users u ON u.id = p.growth_associate_id
            ORDER BY p.submitted_at DESC
            """
        )
        fieldnames = ["referral_id", "prospect_name", "phone_number", "email", "organization", "opportunity_type", "training_interests", "status", "submitted_at", "associate_name"]
        filename = "referrals_report.csv"

    output = []
    output.append(",".join(fieldnames))
    for r in rows:
        row_str = ",".join([f'"{str(r.get(f, "")).replace(chr(34), chr(34)+chr(34))}"' for f in fieldnames])
        output.append(row_str)

    csv_content = "\n".join(output)
    response = make_response(csv_content)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    from pathlib import Path
    from datetime import datetime
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "flask_errors.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"=== ERROR AT {datetime.now()} ===\n")
            f.write(f"Request Path: {request.path}\n")
            f.write(f"Request Method: {request.method}\n")
            f.write(traceback.format_exc())
            f.write("\n\n")
    except Exception:
        pass
    return "Internal Server Error", 500


if __name__ == "__main__":
    app.run(debug=not IS_PRODUCTION)

