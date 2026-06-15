"""OpenAI summarization routes (CSV-driven and individual-record)."""

import os

from flask import Blueprint, jsonify, request

from mrr_ai import state
from mrr_ai.extensions import client
from mrr_ai.prompts import prompts
from mrr_ai.services.ocr import extract_text_from_all_pages, extract_text_from_selected_pages

bp = Blueprint("summarize", __name__)

_TITLE_PROMPT = 'You are an intelligent assistant tasked with extracting the **title** of the document and the **entity responsible for the encounter**. Follow these instructions:\n\n1. **Title Extraction**: \n   - Accurately extract the title of the document if it is explicitly clear. \n   - If the title is not exactly stated, try to infer it from the context of the document; For example, "PT Progress Note", "Office Visit", "Hospital Discharge". The title can be at the top of the document, or towards the end of the document.\n   - If the title cannot be inferred, respond with `" unknown"`. \n   \n2. **Name of Entity Responsible for the Encounter**: \n   - Identify the specific entity responsible for the encounter, which must be the name of the person or the entity. \n   - If available, use the name found in the signature section towards the end of the document to identify the entity responsible for the encounter, or at the top of the document. \n   - Only return the name of the entity that directly conducted the encounter, even if multiple names are mentioned in the text.\n   - Do not return the name of the entity that referred to this encounter or the referral provider. \n   - If no entity name is available, return `"Unknown"`.\n\n3. **Output Format**: \n   - Return the results in a single line, separated by a dash (-):  \n     `[Title] - [Name of Responsible for Encounter]`.\n   - Do not include comma ever in the title. All separations should be done with a dash.\n\n4. **Do Not Add Commentary**: \n   - Do not include explanations, context, or additional text. Return only the extracted information in the required format. '


@bp.route("/summarize", methods=["POST"])
def summarize():
    model = request.json.get("model")

    # Initialize default values
    summaryText = "No Summary Available"
    summaryDate = "No Date"
    summaryTitle = "No Title"
    big_text = "No Data Processed"  # noqa: F841
    # Initialized here too so the return below is safe even if the try block
    # raises before this variable is assigned on the success path.
    big_text_to_show_only = ""

    try:
        with open(state.txt_filepath) as file:
            for line in file:
                # Strip any leading/trailing whitespace and split the line into parts
                line = line.strip()
                if not line:  # Skip empty lines
                    continue

                values = line.split(",")

                # Validate that there are exactly four values
                if len(values) != 6:
                    print(f"Invalid line format: {line}")
                    continue

                # Parse the values into integers
                try:
                    start_page = int(values[0])
                    end_page = int(values[1])
                    document_type = int(values[2])
                    document_date = values[3]
                    doi_from_txt = values[4]
                    state.manual_intervention = values[5]
                except ValueError:
                    print(f"Error parsing numbers in line: {line}")
                    continue

                # Process the values (example: print them)
                print(
                    f"Start Page: {start_page}, End Page: {end_page}, Document Type: {document_type}, date: {document_date}"
                )

                # Add additional processing logic here as needed
                selected_pages = []
                for i in range(start_page, end_page + 1):
                    selected_pages.append(i)

                option = document_type

                if option == 1:
                    system_msg = prompts["category_01"]
                elif option == 2:
                    system_msg = prompts["category_02"]
                elif option == 3:
                    system_msg = prompts["category_03"]
                elif option == 4:
                    system_msg = prompts["category_04"]
                elif option == 5:
                    system_msg = prompts["category_05"]
                elif option == 6:
                    system_msg = prompts["category_06"]
                elif option == 7:
                    system_msg = prompts["category_07"]
                elif option == 8:
                    system_msg = prompts["category_08"]
                elif option == 9:
                    system_msg = prompts["category_09"]
                elif option == 10:
                    system_msg = prompts["category_10"]
                elif option == 11:
                    system_msg = prompts["category_11"]
                elif option == 12:
                    system_msg = prompts["category_12"]
                elif option == 13:
                    system_msg = prompts["category_13"]
                elif option == 14:
                    system_msg = prompts["category_14"]
                elif option == 100:
                    system_msg = prompts["category_100"]
                else:
                    system_msg = prompts["category_100"]

                print("pdf_filepath:", state.pdf_filepath)

                text_to_summarize = extract_text_from_selected_pages(
                    state.pdf_filepath, selected_pages
                )
                print("Text to Summarize:")
                print(text_to_summarize)

                completion = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": [{"type": "text", "text": f"{system_msg}"}]},
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": f"{text_to_summarize}"}],
                        },
                    ],
                    temperature=0.8,
                    max_tokens=2048,
                    top_p=1,
                    frequency_penalty=0,
                    presence_penalty=0,
                    response_format={"type": "text"},
                )

                output = completion.choices[0].message.content

                completion3 = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": [{"type": "text", "text": _TITLE_PROMPT}],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": f"{text_to_summarize}"}],
                        },
                    ],
                    temperature=0.8,
                    max_tokens=2048,
                    top_p=1,
                    frequency_penalty=0,
                    presence_penalty=0,
                    response_format={"type": "text"},
                )

                output_title = completion3.choices[0].message.content

                if doi_from_txt == "-":
                    doi_final = ""
                else:
                    doi_final = f"**DOI**:{doi_from_txt},"

                text_to_add_diag = ""
                if option == 3:
                    text_to_add_diag = " [Diagnostic Study]"
                else:
                    text_to_add_diag = ""

                text_to_add_manual_intervention = ""
                if state.manual_intervention == "x" or state.manual_intervention == "X":
                    text_to_add_manual_intervention = "[ManualCheck] "
                else:
                    text_to_add_manual_intervention = ""
                print("text_to_add_manual_intervention", text_to_add_manual_intervention)

                output_dict = {
                    "summaryDate": document_date,
                    "summaryTitle": text_to_add_manual_intervention
                    + output_title
                    + text_to_add_diag
                    + f" (Pages {start_page}-{end_page})",
                    "manualCheck": text_to_add_manual_intervention,
                    "summaryText": f"{doi_final} {output}",
                }

                state.all_data.append(output_dict)
                with open("all_data_temp.txt", "w") as file:
                    file.write(str(state.all_data))
                print("all dataaaaaaaaaaaaaa", state.all_data)

            big_text_to_show_only = ""

            for item in state.all_data:
                # Extract values
                summaryDate = item.get("summaryDate", "No Date")
                manualCheck = item.get("manualCheck", "-")  # noqa: F841
                summaryTitle = item.get("summaryTitle", "No Title")
                summaryText = item.get("summaryText", "No Output")

                big_text_to_show_only += f"_{summaryDate}_\n{summaryTitle}\n{summaryText}\n\n"

    except FileNotFoundError:
        print(f"File not found: {state.txt_filepath}")
        big_text_to_show_only = (
            f"ERROR: page-range file not found ({state.txt_filepath}). Upload the CSV/TXT first."
        )
    except Exception as e:
        print(f"An error occurred: {e}")
        big_text_to_show_only = f"ERROR during summarization: {e}"

    return {
        "summaryText": summaryText,
        "summaryDate": summaryDate,
        "summaryTitle": summaryTitle,
        "big_text": big_text_to_show_only,
    }  # we are only using big text to show though


@bp.route("/summarize_indiv_record", methods=["POST"])
def summarize_indiv_record():
    print("we are here 1")

    state.all_data = []

    model = "gpt-4o-mini"

    data = request.json  # Get JSON data from request
    print(data)
    folder_name = data.get("folder_name")  # noqa: F841
    records = data.get("records", [])

    if not records:
        return jsonify({"error": "No records received"}), 400

    summary_results = []  # noqa: F841

    for record in records:
        print("Starting RECORD")
        print("--------------------")
        filename = record.get("filename", "Unknown")
        category = record.get("category", "100")
        encounter_date = record.get("encounter_date", "01/01/1900")
        injury_date = record.get("injury_date", "01/01/1900")
        manual_review = record.get("manual_review", "-")
        pages = record.get("pages", "-")

        # Construct full file path
        full_path = os.path.join(state.indiv_mrr_folder_path, filename)
        print("fl", full_path)

        # Print record details for debugging
        print(f"Processing record: {full_path}")
        print(
            f"Category: {category}, Encounter Date: {encounter_date}, Injury Date: {injury_date}, Manual Review: {manual_review}, Pages: {pages}"
        )

        summaryText = "No Summary Available"
        summaryDate = "No Date"
        summaryTitle = "No Title"
        big_text = "No Data Processed"  # noqa: F841

        option = category

        try:
            if option == 1 or "1":
                system_msg = prompts["category_01"]
            elif option == 2 or "2":
                system_msg = prompts["category_02"]
            elif option == 3 or "3":
                print("here")
                system_msg = prompts["category_03"]
            elif option == 4 or "4":
                system_msg = prompts["category_04"]
            elif option == 5 or "5":
                system_msg = prompts["category_05"]
            elif option == 6 or "6":
                system_msg = prompts["category_06"]
            elif option == 7 or "7":
                system_msg = prompts["category_07"]
            elif option == 8 or "8":
                system_msg = prompts["category_08"]
            elif option == 9 or "9":
                system_msg = prompts["category_09"]
            elif option == 10 or "10":
                system_msg = prompts["category_10"]
            elif option == 11 or "11":
                system_msg = prompts["category_11"]
            elif option == 12 or "12":
                system_msg = prompts["category_12"]
            elif option == 13 or "13":
                system_msg = prompts["category_13"]
            elif option == 14 or "14":
                system_msg = prompts["category_14"]
            elif option == 100 or "100":
                system_msg = prompts["category_100"]
            else:
                system_msg = prompts["category_100"]

            print("pdf_filepath:", full_path)

            text_to_summarize = extract_text_from_all_pages(full_path)
            # print('Text to Summarize:')
            print(text_to_summarize)
        except:  # noqa: E722
            print("except")

        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": [{"type": "text", "text": f"{system_msg}"}]},
                {"role": "user", "content": [{"type": "text", "text": f"{text_to_summarize}"}]},
            ],
            temperature=0.8,
            max_tokens=2048,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
            response_format={"type": "text"},
        )

        output = completion.choices[0].message.content

        completion3 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": [{"type": "text", "text": _TITLE_PROMPT}]},
                {"role": "user", "content": [{"type": "text", "text": f"{text_to_summarize}"}]},
            ],
            temperature=0.8,
            max_tokens=2048,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
            response_format={"type": "text"},
        )

        output_title = completion3.choices[0].message.content

        if injury_date == "-" or injury_date == "":
            doi_final = ""
        else:
            doi_final = f"**DOI**:{injury_date},"

        text_to_add_diag = ""
        if category == 3 or category == "3":
            text_to_add_diag = " [Diagnostic Study]"
        else:
            text_to_add_diag = ""

        text_to_add_manual_intervention = ""
        if manual_review == "x" or manual_review == "X":
            text_to_add_manual_intervention = "[ManualCheck] "
        else:
            text_to_add_manual_intervention = ""

        output_dict = {
            "summaryDate": encounter_date,
            "summaryTitle": text_to_add_manual_intervention
            + output_title
            + text_to_add_diag
            + f" (Pages: {pages})",
            "manualCheck": text_to_add_manual_intervention,
            "summaryText": f"{doi_final} {output}",
        }

        state.all_data.append(output_dict)
        with open("all_data_temp.txt", "w") as file:
            file.write(str(state.all_data))
        print("")

        big_text_to_show_only = ""

        for item in state.all_data:
            # Extract values
            summaryDate = item.get("summaryDate", "No Date")
            manualCheck = item.get("manualCheck", "-")  # noqa: F841
            summaryTitle = item.get("summaryTitle", "No Title")
            summaryText = item.get("summaryText", "No Output")

            big_text_to_show_only += f"_{summaryDate}_\n{summaryTitle}\n{summaryText}\n\n"

    print("all dataaaaaaaaaaaaaa", state.all_data)

    return "S"
