"""Individual-MRR patient folder creation and multi-file upload."""

import os

from flask import Blueprint, jsonify, request

from mrr_ai import state
from mrr_ai.config import UPLOAD_BASE_DIR
from mrr_ai.services.files import safe_name

bp = Blueprint("individual_mrr", __name__)


@bp.route("/create_patient_folder_indiv_mrr", methods=["POST"])
def create_patient_folder():
    """Creates a patient folder but does not upload files."""
    data = request.json
    folder_name = data.get("folder_name")
    patientName = data.get("patient_name")

    # patientNameGlobal is a display value (used in document text), kept raw.
    state.patientNameGlobal = patientName

    if not folder_name:
        return jsonify({"error": "Invalid folder name"}), 400

    # Sanitize before building a path (prevents traversal via the folder name).
    folder_path = os.path.join(UPLOAD_BASE_DIR, safe_name(folder_name))

    try:
        os.makedirs(folder_path, exist_ok=True)  # Create the directory if it doesn't exist
        return jsonify(
            {"message": f"Folder '{folder_name}' created successfully", "folder_path": folder_path}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/upload_files", methods=["POST"])
def upload_files():
    """Uploads files to the already created patient folder."""
    patient_folder = request.form.get("folder_name")  # Get the folder name from form data

    if not patient_folder:
        return jsonify({"error": "Missing patient folder name"}), 400

    folder_path = os.path.join(UPLOAD_BASE_DIR, safe_name(patient_folder))
    state.indiv_mrr_folder_path = folder_path

    if not os.path.exists(folder_path):
        return jsonify({"error": "Patient folder does not exist"}), 400

    files = request.files.getlist("pdfs")  # Retrieve multiple files from the request

    saved_files = []
    for file in files:
        if file:
            filename = safe_name(file.filename)  # Sanitize each uploaded filename
            file_path = os.path.join(folder_path, filename)  # Save in the patient folder
            file.save(file_path)
            saved_files.append(filename)

    return jsonify(
        {
            "message": "Files uploaded successfully",
            "saved_files": saved_files,
            "folder_path": folder_path,
        }
    )
