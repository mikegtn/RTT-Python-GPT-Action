"""Windows-native Tkinter GUI for the RTT departure board."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from .cli import load_dotenv
from .client import RTTClient, RTTError
from .departures import DepartureBoard, next_departures


class RTTDepartureApp(tk.Tk):
    """Simple desktop departure board backed by the RTT API."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Realtime Trains Departure Board")
        self.geometry("1050x500")
        self.minsize(780, 400)
        self._results: queue.Queue[tuple[str, Any]] = queue.Queue()

        load_dotenv()
        self.station = tk.StringVar()
        self.token = tk.StringVar(value=os.environ.get("RTT_TOKEN", ""))
        self.count = tk.IntVar(value=5)
        self.minutes = tk.IntVar(value=180)
        self.status = tk.StringVar(value="Enter a station name and select Find trains.")

        self._build_ui()
        self.after(100, self._poll_results)
        self.bind("<Return>", lambda _event: self.find_trains())

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(3, weight=1)

        ttk.Label(outer, text="Station").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        station_entry = ttk.Entry(outer, textvariable=self.station)
        station_entry.grid(row=0, column=1, columnspan=4, sticky=tk.EW, pady=4)
        station_entry.focus_set()

        ttk.Label(outer, text="RTT token").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        self.token_entry = ttk.Entry(outer, textvariable=self.token, show="•")
        self.token_entry.grid(row=1, column=1, sticky=tk.EW, pady=4)
        self.show_token = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            outer,
            text="Show",
            variable=self.show_token,
            command=lambda: self.token_entry.configure(show="" if self.show_token.get() else "•"),
        ).grid(row=1, column=2, padx=8)

        ttk.Label(outer, text="Trains").grid(row=1, column=3, padx=(12, 4))
        ttk.Spinbox(outer, from_=1, to=20, width=4, textvariable=self.count).grid(row=1, column=4)
        ttk.Label(outer, text="Within (minutes)").grid(row=1, column=5, padx=(12, 4))
        ttk.Spinbox(outer, from_=1, to=1439, width=6, textvariable=self.minutes).grid(row=1, column=6)

        self.find_button = ttk.Button(outer, text="Find trains", command=self.find_trains)
        self.find_button.grid(row=0, column=5, columnspan=2, sticky=tk.EW, padx=(12, 0), pady=4)

        ttk.Separator(outer).grid(row=2, column=0, columnspan=7, sticky=tk.EW, pady=12)

        columns = ("scheduled", "expected", "destination", "platform", "allocation", "status")
        self.table = ttk.Treeview(outer, columns=columns, show="headings", selectmode="browse")
        headings = {
            "scheduled": "Time",
            "expected": "Expected",
            "destination": "Destination",
            "platform": "Platform",
            "allocation": "Allocated train",
            "status": "Status",
        }
        widths = {
            "scheduled": 70,
            "expected": 85,
            "destination": 220,
            "platform": 70,
            "allocation": 220,
            "status": 110,
        }
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(
                column,
                width=widths[column],
                minwidth=60,
                anchor=tk.W if column in {"destination", "allocation", "status"} else tk.CENTER,
            )
        self.table.grid(row=3, column=0, columnspan=7, sticky=tk.NSEW)

        scrollbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=self.table.yview)
        scrollbar.grid(row=3, column=7, sticky=tk.NS)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.tag_configure("cancelled", foreground="#a40000")
        self.table.tag_configure("late", foreground="#9a5b00")

        ttk.Label(outer, textvariable=self.status, relief=tk.SUNKEN, anchor=tk.W).grid(
            row=4, column=0, columnspan=8, sticky=tk.EW, pady=(12, 0)
        )

    def find_trains(self) -> None:
        station = self.station.get().strip()
        token = self.token.get().strip()
        try:
            count = self.count.get()
            minutes = self.minutes.get()
        except tk.TclError:
            messagebox.showerror("Invalid options", "Trains and minutes must be whole numbers.")
            return
        if not station:
            messagebox.showwarning("Station required", "Enter a station name or CRS code.")
            return
        if not token:
            messagebox.showwarning(
                "RTT token required",
                "Paste the access or refresh token from the RTT API portal.",
            )
            return
        if not 1 <= count <= 20 or not 1 <= minutes <= 1439:
            messagebox.showerror(
                "Invalid options",
                "Trains must be 1–20 and minutes must be 1–1439.",
            )
            return

        self.find_button.configure(state=tk.DISABLED)
        self.status.set(f"Looking up departures from {station}…")
        self._clear_table()
        worker = threading.Thread(
            target=self._fetch,
            args=(token, station, count, minutes),
            daemon=True,
        )
        worker.start()

    def _fetch(self, token: str, station: str, count: int, minutes: int) -> None:
        try:
            client = RTTClient(
                token,
                base_url=os.environ.get("RTT_BASE_URL", "https://data.rtt.io"),
                api_version=os.environ.get("RTT_API_VERSION"),
                token_type=os.environ.get("RTT_TOKEN_TYPE", "auto"),
            )
            board = next_departures(client, station, limit=count, minutes=minutes)
            self._results.put(("success", board))
        except (RTTError, ValueError, OSError) as exc:
            self._results.put(("error", str(exc)))

    def _poll_results(self) -> None:
        try:
            kind, result = self._results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.find_button.configure(state=tk.NORMAL)
            if kind == "success":
                self._show_board(result)
            else:
                self.status.set("Unable to load departures.")
                messagebox.showerror("Realtime Trains", result)
        self.after(100, self._poll_results)

    def _clear_table(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)

    def _show_board(self, board: DepartureBoard) -> None:
        self._clear_table()
        for departure in board.departures:
            status_lower = departure.status.casefold()
            tag = "cancelled" if status_lower == "cancelled" else "late" if "late" in status_lower else ""
            self.table.insert(
                "",
                tk.END,
                values=(
                    departure.scheduled,
                    departure.expected,
                    departure.destination,
                    departure.platform,
                    departure.allocation,
                    departure.status,
                ),
                tags=(tag,) if tag else (),
            )
        if board.departures:
            self.status.set(
                f"Showing {len(board.departures)} departure(s) from {board.station} ({board.station_code})."
            )
        else:
            self.status.set(
                f"No departures found from {board.station} ({board.station_code}) in this time window."
            )


def main() -> None:
    app = RTTDepartureApp()
    app.mainloop()


if __name__ == "__main__":
    main()

