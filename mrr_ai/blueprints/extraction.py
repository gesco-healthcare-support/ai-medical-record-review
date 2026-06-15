"""LLM extraction of patient name/DOB and law firm from the record."""

import json

from flask import Blueprint

from mrr_ai import state
from mrr_ai.extensions import client
from mrr_ai.services.ocr import extract_text_from_selected_pages

bp = Blueprint("extraction", __name__)


@bp.route("/getpatientnameanddob", methods=["POST"])
def getpatientnameanddob():
    text_to_summarize = extract_text_from_selected_pages(state.pdf_filepath, [5, 15])

    completion3 = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You are an assistant that will extract the name of the patient and their DOB from the text and return it in a JSON format with name and dob as the keys. Make the DOB format mm/dd/yyyy",
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract the name of the patient and their date of birth (DOB) from this text: "
                        + text_to_summarize,
                    }
                ],
            },
        ],
        temperature=1,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        response_format={"type": "text"},
    )

    output = completion3.choices[0].message.content
    clean_response = output.replace("```json", "").replace("```", "").strip()
    print(clean_response)

    json_data = json.loads(clean_response)
    name = json_data.get("name")
    dob = json_data.get("dob")

    print(name, dob)
    return {"name": name, "dob": dob}


@bp.route("/getlawfirm", methods=["POST"])
def getlawfirm():
    text_to_summarize = extract_text_from_selected_pages(state.pdf_filepath, [1, 7])

    completion3 = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You are an assistant that will extract the name of the lawyer or attorney sending the document, as well as the name of the law firm they represent",
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract the name of the attorney and the law firm it represents and return it in a JSON format with the key 'lawfirm' and the value being the name of the attorney, followed by 'from' the name of lawfirm. The name of the attorney and the law firm is the declaration page. (Note that this name is different than the doctor). Use this text: "
                        + text_to_summarize,
                    }
                ],
            },
        ],
        temperature=1,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        response_format={"type": "text"},
    )

    output = completion3.choices[0].message.content
    clean_response = output.replace("```json", "").replace("```", "").strip()
    print(clean_response)

    json_data = json.loads(clean_response)
    lawfirm = json_data.get("lawfirm")

    print(lawfirm, lawfirm)
    return {"lawfirm": lawfirm}
