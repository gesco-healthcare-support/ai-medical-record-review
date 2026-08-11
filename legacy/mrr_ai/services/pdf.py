"""PDF utilities: sizing, page counts, and chunk segmentation."""

import os

from pypdf import PdfReader, PdfWriter

from mrr_ai.config import UPLOAD_FOLDER


def get_pdf_size(filepath):
    """Returns the size of the PDF file in megabytes."""
    file_size = os.path.getsize(filepath)  # Get size in bytes
    size_in_mb = file_size / (1024 * 1024)  # Convert bytes to megabytes
    return size_in_mb


def get_pdf_page_count(pdf_file):
    try:
        # Open the PDF file
        reader = PdfReader(pdf_file)
        # Get the total number of pages
        total_pages = len(reader.pages)
        return total_pages
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return None


def segment_pdf(input_pdf, pages_per_segment=100):
    # Read the PDF file
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)
    base_name = os.path.splitext(os.path.basename(input_pdf))[
        0
    ]  # Get the base file name without extension

    # Define the main folder path using base_name
    file_path = os.path.join(os.path.expanduser("~"), "MRRs")
    # Define the segmented folder path inside the main folder
    output_folder = os.path.join(file_path, f"{base_name}_segmented")

    # Create the output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)

    # Calculate the total number of segments for formatting
    segment_count = 1
    for start_page in range(0, total_pages, pages_per_segment):
        writer = PdfWriter()
        end_page = min(
            start_page + pages_per_segment, total_pages
        )  # Ensure we don't exceed total pages

        # Add pages to the segment
        for page_num in range(start_page, end_page):
            writer.add_page(reader.pages[page_num])

        # Save the segment with leading zeros in the segment count
        output_file = os.path.join(output_folder, f"{base_name}_{segment_count:02}.pdf")
        with open(output_file, "wb") as output_pdf:
            writer.write(output_pdf)
        print(f"Saved: {output_file}")
        segment_count += 1
    print(f"Segmentation complete. Files saved in folder: {output_folder}")


def segment_pdf_locally(input_pdf, pages_per_segment=100):
    # Read the PDF file
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)
    base_name = os.path.splitext(os.path.basename(input_pdf))[
        0
    ]  # Get the base file name without extension

    # Define the main folder path using the configured upload folder
    file_path = os.path.join(UPLOAD_FOLDER)

    # Define the segmented folder path inside the main folder
    output_folder = os.path.join(file_path, f"{base_name}_segmented")

    # Create the output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)

    # Initialize a list to store created file paths
    created_files = []

    # Calculate the total number of segments for formatting
    segment_count = 1
    for start_page in range(0, total_pages, pages_per_segment):
        writer = PdfWriter()
        end_page = min(
            start_page + pages_per_segment, total_pages
        )  # Ensure we don't exceed total pages

        # Add pages to the segment
        for page_num in range(start_page, end_page):
            writer.add_page(reader.pages[page_num])

        # Save the segment with leading zeros in the segment count
        output_file = os.path.join(output_folder, f"{base_name}_{segment_count:02}.pdf")
        with open(output_file, "wb") as output_pdf:
            writer.write(output_pdf)

        # Add the file path to the list
        created_files.append(output_file)
        print(f"Saved: {output_file}")
        segment_count += 1

    # Sort the list of created files alphabetically
    created_files.sort()

    print(f"Segmentation complete. Files saved in folder: {output_folder}")
    print(created_files)

    # Return the sorted list of files
    return created_files
