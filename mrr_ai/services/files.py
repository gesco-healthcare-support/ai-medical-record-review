"""Small file/date helpers used by the upload and CSV-check routes."""

from datetime import datetime

from mrr_ai.config import ALLOWED_EXTENSIONS


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%m/%d/%Y")
    except ValueError:
        return datetime.min  # Fallback for invalid dates


def is_valid_date(date_str, date_format="%m/%d/%Y"):
    try:
        if date_str.strip() == "-":
            return True  # Skip validation for "-"
        datetime.strptime(date_str, date_format)
        return True
    except ValueError:
        return False


def count_lines_in_file(file_path):
    try:
        with open(file_path) as file:
            lines = file.readlines()
            return len(lines)
    except Exception as e:
        print(f"Error: {e}")
        return 0
