/** The layout of a dialog that can be taller than the window: the received-files upload and the
 *  billing sheet. `DIALOG_FRAME` (on `DialogContent`) keeps the dialog inside the window and stacks
 *  its header, body and footer; `DIALOG_BODY` (on the body) is the only part that scrolls. So the
 *  footer's buttons stay in view whatever the window's height or the browser's zoom - zoomed in, the
 *  dialog used to run past the bottom of the window with Combine & upload below it (the office's
 *  first use, 2026-11-20). Also widens the dialog from the default `sm:max-w-sm`, which a plain
 *  `max-w-2xl` did not override on a wide screen. */
export const DIALOG_FRAME = "flex max-h-[calc(100dvh-2rem)] flex-col sm:max-w-2xl";
export const DIALOG_BODY =
  "-mx-4 grid min-h-0 flex-1 content-start gap-4 overflow-y-auto px-4 py-1";
