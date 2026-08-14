"""Guided desktop installer for nontechnical users."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .installer import client_targets, install


def launch() -> None:
    root = tk.Tk()
    root.title("Lians Memory Setup")
    root.geometry("620x560")
    root.minsize(560, 500)

    frame = ttk.Frame(root, padding=28)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Give your AI a memory", font=("Segoe UI", 21, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text=(
            "Lians saves useful facts locally and recalls only what matters. "
            "No account, API key, model download, or configuration editing required."
        ),
        wraplength=550,
    ).pack(anchor="w", pady=(10, 22))
    ttk.Label(frame, text="Choose where to add Lians", font=("Segoe UI", 12, "bold")).pack(
        anchor="w"
    )

    targets = client_targets()
    choices: dict[str, tk.BooleanVar] = {}
    for target in targets.values():
        variable = tk.BooleanVar(value=target.detected)
        choices[target.key] = variable
        status_label = "configured" if target.configured else "found" if target.detected else ""
        label = target.label + (f"  ({status_label})" if status_label else "")
        ttk.Checkbutton(frame, text=label, variable=variable).pack(anchor="w", pady=5)

    note = ttk.Label(
        frame,
        text=(
            "Your memory stays on this computer. Existing configuration files are backed up. "
            "ChatGPT requires a hosted connector and is not changed by this installer."
        ),
        wraplength=550,
        foreground="#555555",
    )
    note.pack(anchor="w", pady=(18, 16))
    status = tk.StringVar(value="Ready")
    ttk.Label(frame, textvariable=status).pack(anchor="w", pady=(0, 8))

    def begin_install() -> None:
        selected = [key for key, value in choices.items() if value.get()]
        if not selected:
            messagebox.showinfo("Choose an AI client", "Select at least one AI client first.")
            return
        button.configure(state="disabled")
        status.set("Installing Lians and backing up your settings…")
        root.update_idletasks()
        try:
            result = install(selected)
        except (OSError, TypeError, ValueError) as error:
            failed(str(error))
            return
        finished(result["next_step"])

    def failed(detail: str) -> None:
        status.set("Setup could not finish.")
        button.configure(state="normal")
        messagebox.showerror("Lians setup", detail)

    def finished(next_step: str) -> None:
        status.set("Lians is installed.")
        button.configure(text="Installed", state="disabled")
        messagebox.showinfo("Lians is ready", next_step)

    button = ttk.Button(frame, text="Install Lians", command=begin_install)
    button.pack(anchor="w", ipadx=20, ipady=8)
    ttk.Label(
        frame,
        text="After restarting your AI client, try: “Remember that I am researching sustainable packaging.”",
        wraplength=550,
    ).pack(anchor="w", pady=(22, 0))
    root.mainloop()
