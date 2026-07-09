"""OCR text extraction via Tesseract (pages rasterized by Poppler/pdf2image)."""

import pytesseract
from pdf2image import convert_from_path

from mrr_ai.config import TESSERACT_CMD

# Tesseract is often installed but not on PATH (Windows installer default); an empty
# extraction here silently starves summarization, so honor the explicit override.
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def extract_text_from_selected_pages(pdf_path, selected_pages):
    extracted_text = ""

    # Sort the selected pages to optimize page range extraction
    selected_pages = sorted(set(selected_pages))  # Ensure no duplicates and sort pages

    # Convert only the required pages to images
    for page_number in selected_pages:
        try:
            # Convert the specific page to an image (1-indexed)
            images = convert_from_path(pdf_path, first_page=page_number, last_page=page_number)

            # Process the single image returned for this page
            for page_image in images:
                print(f"Processing page {page_number} using PyTesseract")

                # Perform OCR on the image
                ocr_text = pytesseract.image_to_string(page_image)
                # extracted_text += f'Page {page_number}:\n{ocr_text}\n'
                extracted_text += f"{ocr_text}"

        except Exception as e:
            print(f"Error processing page {page_number}: {e}")

    return extracted_text


def extract_text_from_all_pages(pdf_path):
    extracted_text = ""

    try:
        # Convert all pages to images
        images = convert_from_path(pdf_path)

        # Process each page image
        for page_number, page_image in enumerate(images, start=1):
            print(f"Processing page {page_number} using PyTesseract")

            # Perform OCR on the image
            ocr_text = pytesseract.image_to_string(page_image)
            extracted_text += f"Page {page_number}:\n{ocr_text}\n"

    except Exception as e:
        print(f"Error processing PDF: {e}")

    return extracted_text
