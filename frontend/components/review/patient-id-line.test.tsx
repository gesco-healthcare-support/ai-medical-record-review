import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const setPatientId = vi.fn();
vi.mock("@/lib/documents-api", () => ({
  setPatientId: (...args: unknown[]) => setPatientId(...args),
}));

import { ApiError } from "@/lib/api";
import { PatientIdLine } from "./patient-id-line";

afterEach(() => vi.clearAllMocks());

describe("PatientIdLine", () => {
  it("says how many other records carry the patient's ID", () => {
    render(<PatientIdLine documentId="d1" patientId="801234" others={2} onSaved={vi.fn()} />);
    expect(screen.getByLabelText("Patient ID")).toHaveValue("801234");
    expect(screen.getByText("This patient has 2 other records in the system.")).toBeInTheDocument();
  });

  it("says so when the patient has no other record", () => {
    render(<PatientIdLine documentId="d1" patientId="801234" others={0} onSaved={vi.fn()} />);
    expect(screen.getByText("No other records for this patient.")).toBeInTheDocument();
  });

  it("asks for the ID when the record has none", () => {
    render(<PatientIdLine documentId="d1" patientId="" others={0} onSaved={vi.fn()} />);
    expect(
      screen.getByText("No patient ID yet - enter it to find this patient's other records."),
    ).toBeInTheDocument();
  });

  it("saves the typed ID, cleaned, when the box loses focus, and reports the new count", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    setPatientId.mockResolvedValueOnce({ patient_id: "AB1234", patient_records: 1 });
    render(<PatientIdLine documentId="d1" patientId="" others={0} onSaved={onSaved} />);
    await user.type(screen.getByLabelText("Patient ID"), "ab-1234");
    await user.tab();
    await waitFor(() => expect(setPatientId).toHaveBeenCalledWith("d1", "AB1234"));
    expect(onSaved).toHaveBeenCalledWith({ id: "AB1234", others: 1 });
  });

  it("saves on Enter too", async () => {
    const user = userEvent.setup();
    setPatientId.mockResolvedValueOnce({ patient_id: "801234", patient_records: 0 });
    render(<PatientIdLine documentId="d1" patientId="" others={0} onSaved={vi.fn()} />);
    await user.type(screen.getByLabelText("Patient ID"), "801234{Enter}");
    await waitFor(() => expect(setPatientId).toHaveBeenCalledWith("d1", "801234"));
  });

  it("sends nothing when the ID did not change", async () => {
    const user = userEvent.setup();
    render(<PatientIdLine documentId="d1" patientId="801234" others={0} onSaved={vi.fn()} />);
    await user.click(screen.getByLabelText("Patient ID"));
    await user.tab();
    expect(setPatientId).not.toHaveBeenCalled();
  });

  it("says why when the server refused the ID", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    setPatientId.mockRejectedValueOnce(new ApiError("the ID could not be saved", 400));
    render(<PatientIdLine documentId="d1" patientId="" others={0} onSaved={onSaved} />);
    await user.type(screen.getByLabelText("Patient ID"), "801234");
    await user.tab();
    expect(await screen.findByText(/Not saved: the ID could not be saved/)).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });
});
