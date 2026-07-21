import { cn } from "@/lib/utils";

export type StepId = "identify" | "review" | "summaries";

const STEPS: { id: StepId; num: string; label: string }[] = [
  { id: "identify", num: "1", label: "Identify documents" },
  { id: "review", num: "2", label: "Review & correct" },
  { id: "summaries", num: "3", label: "Summaries" },
];
const ORDER: StepId[] = ["identify", "review", "summaries"];

/** The 3-step pipeline stepper (DS .ev-stepper). Steps before the active one read as done
 *  (green check), after it as upcoming; all disabled while a job is running. */
export function Stepper({
  activeStep,
  busy,
  onStep,
}: {
  activeStep: StepId;
  busy: boolean;
  onStep: (step: StepId) => void;
}) {
  const activeIdx = ORDER.indexOf(activeStep);
  return (
    <nav className="ev-stepper" aria-label="Pipeline steps">
      {STEPS.map((step, i) => {
        const idx = ORDER.indexOf(step.id);
        return (
          <div key={step.id} className="contents">
            {i > 0 ? <span className="ev-step-line" aria-hidden /> : null}
            <button
              type="button"
              className={cn(
                "ev-step",
                idx < activeIdx && "done",
                idx === activeIdx && "active",
                busy && "busy",
              )}
              disabled={busy}
              aria-current={idx === activeIdx ? "step" : undefined}
              onClick={() => onStep(step.id)}
            >
              <span className="ev-step-circle" data-num={step.num} aria-hidden />
              <span className="ev-step-label">{step.label}</span>
            </button>
          </div>
        );
      })}
    </nav>
  );
}
