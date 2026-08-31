"""Windows-native chat interface for the GPT-powered rail assistant."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

from .cli import load_dotenv
from .client import RTTClient, RTTError
from .rail_assistant import RailAssistant, RailAssistantError


class RailAssistantApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RTT Rail Assistant")
        self.geometry("900x680")
        self.minsize(700, 520)
        load_dotenv()

        self.openai_key = tk.StringVar(value=os.environ.get("OPENAI_API_KEY", ""))
        self.rtt_token = tk.StringVar(value=os.environ.get("RTT_TOKEN", ""))
        self.model = tk.StringVar(value=os.environ.get("OPENAI_MODEL", "gpt-5.4"))
        self.status = tk.StringVar(value="Ready")
        self._assistant: RailAssistant | None = None
        self._assistant_config: tuple[str, str, str] | None = None
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._build_ui()
        self.after(100, self._poll)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(2, weight=1)

        ttk.Label(outer, text="OpenAI API key").grid(row=0, column=0, sticky=tk.W, padx=(0, 7), pady=3)
        ttk.Entry(outer, textvariable=self.openai_key, show="•").grid(row=0, column=1, sticky=tk.EW, pady=3)
        ttk.Label(outer, text="RTT token").grid(row=1, column=0, sticky=tk.W, padx=(0, 7), pady=3)
        ttk.Entry(outer, textvariable=self.rtt_token, show="•").grid(row=1, column=1, sticky=tk.EW, pady=3)
        ttk.Label(outer, text="Model").grid(row=0, column=2, padx=(12, 4))
        ttk.Entry(outer, textvariable=self.model, width=14).grid(row=0, column=3, sticky=tk.EW)
        ttk.Button(outer, text="New chat", command=self.new_chat).grid(row=1, column=2, columnspan=2, sticky=tk.EW, padx=(12, 0))

        self.chat = scrolledtext.ScrolledText(outer, wrap=tk.WORD, state=tk.DISABLED, font=("Segoe UI", 10))
        self.chat.grid(row=2, column=0, columnspan=4, sticky=tk.NSEW, pady=(12, 8))
        self.chat.tag_configure("user", foreground="#124b8c", font=("Segoe UI Semibold", 10))
        self.chat.tag_configure("assistant", foreground="#126b36", font=("Segoe UI Semibold", 10))

        self.question = scrolledtext.ScrolledText(outer, wrap=tk.WORD, height=4, font=("Segoe UI", 10))
        self.question.grid(row=3, column=0, columnspan=3, sticky=tk.EW)
        self.question.bind("<Control-Return>", self._send_event)
        self.send_button = ttk.Button(outer, text="Ask\n(Ctrl+Enter)", command=self.send)
        self.send_button.grid(row=3, column=3, sticky=tk.NSEW, padx=(8, 0))
        ttk.Label(outer, textvariable=self.status, relief=tk.SUNKEN, anchor=tk.W).grid(
            row=4, column=0, columnspan=4, sticky=tk.EW, pady=(8, 0)
        )
        self._append("RTT Rail Assistant", "Ask about departures, allocations, delays, cancellations, formations, or incoming workings.\n", "assistant")

    def _append(self, label: str, text: str, tag: str) -> None:
        self.chat.configure(state=tk.NORMAL)
        self.chat.insert(tk.END, f"{label}:\n", tag)
        self.chat.insert(tk.END, text.rstrip() + "\n\n")
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)

    def _send_event(self, _event: tk.Event[Any]) -> str:
        self.send()
        return "break"

    def _get_assistant(self) -> RailAssistant:
        openai_key = self.openai_key.get().strip()
        rtt_token = self.rtt_token.get().strip()
        model = self.model.get().strip() or "gpt-5.4"
        if not openai_key:
            raise ValueError("Enter OPENAI_API_KEY")
        if not rtt_token:
            raise ValueError("Enter RTT_TOKEN")
        config = (openai_key, rtt_token, model)
        if self._assistant is None or self._assistant_config != config:
            client = RTTClient(
                rtt_token,
                base_url=os.environ.get("RTT_BASE_URL", "https://data.rtt.io"),
                api_version=os.environ.get("RTT_API_VERSION"),
                token_type=os.environ.get("RTT_TOKEN_TYPE", "auto"),
                timeout=45,
            )
            self._assistant = RailAssistant(openai_key, client, model=model)
            self._assistant_config = config
        return self._assistant

    def send(self) -> None:
        question = self.question.get("1.0", tk.END).strip()
        if not question:
            return
        try:
            assistant = self._get_assistant()
        except ValueError as exc:
            messagebox.showwarning("Configuration required", str(exc))
            return
        self.question.delete("1.0", tk.END)
        self._append("You", question, "user")
        self.send_button.configure(state=tk.DISABLED)
        self.status.set("Checking rail data and composing an answer…")
        threading.Thread(target=self._ask, args=(assistant, question), daemon=True).start()

    def _ask(self, assistant: RailAssistant, question: str) -> None:
        try:
            self._events.put(("answer", assistant.ask(question)))
        except (RailAssistantError, RTTError, ValueError, OSError) as exc:
            self._events.put(("error", str(exc)))

    def _poll(self) -> None:
        try:
            kind, value = self._events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.send_button.configure(state=tk.NORMAL)
            self.status.set("Ready" if kind == "answer" else "Request failed")
            if kind == "answer":
                self._append("Assistant", str(value), "assistant")
            else:
                messagebox.showerror("RTT Rail Assistant", str(value))
        self.after(100, self._poll)

    def new_chat(self) -> None:
        if self._assistant:
            self._assistant.reset()
        self.chat.configure(state=tk.NORMAL)
        self.chat.delete("1.0", tk.END)
        self.chat.configure(state=tk.DISABLED)
        self._append("RTT Rail Assistant", "New conversation started.\n", "assistant")


def main() -> None:
    RailAssistantApp().mainloop()


if __name__ == "__main__":
    main()

