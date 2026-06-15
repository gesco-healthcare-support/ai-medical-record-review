"""Shared mutable application state.

These module-level globals were lifted verbatim from the original single-file app so
behavior is preserved. Access them as ``state.<name>`` and rebind as ``state.x = ...``
so the change is visible to every importer. Do NOT ``from mrr_ai.state import x`` -
that copies the binding and writes would not propagate.

This intentionally keeps the original single-process design. Replacing it with a proper
per-session store is tracked as separate follow-up work (the app must run single-process
until then).
"""

pdf_filepath = None
txt_filepath = None
pdf_savepath = "/home/usera/mrr-line/uploads/"
main_filename = "summary"
main_txt_filename = "txt_pages"
patientNameGlobal = "Patient Full Name"
pages_not_counting = 0
num_pages = 0
all_data = []
manual_intervention = ""
indiv_mrr_folder_path = ""
sorted_file_paths = []
