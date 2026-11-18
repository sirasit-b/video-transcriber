"""ถอดเสียงจากวิดีโอ/ไฟล์เสียงด้วย OpenAI API พร้อมประเมินค่าใช้จ่าย
รัน: python app.py      ทดสอบ: python app.py --selftest
ต้องมี ffmpeg/ffprobe ใน PATH และ pip install openai
"""
import os, re, subprocess, sys, tempfile, threading, glob, json, datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# shortcut: ราคา USD/นาที ตามหน้า pricing ของ OpenAI ณ ตอนเขียน, ถ้า OpenAI เปลี่ยนราคาให้แก้ตรงนี้
PRICES = {
    "gpt-4o-mini-transcribe": 0.003,
    "gpt-4o-transcribe": 0.006,
    "whisper-1": 0.006,
}
CHUNK_SEC = 600  # ตัดทีละ 10 นาที ให้ไฟล์ต่ำกว่าลิมิต 25MB และความยาวต่อคำขอของโมเดล
MEDIA = "*.mp4 *.mov *.mkv *.avi *.webm *.mp3 *.wav *.m4a *.aac *.flac *.ogg"

# ธีมสีขาว
BG, CARD, BORDER = "#ffffff", "#f7f8fa", "#e5e7eb"
TEXT, MUTED, ACCENT, ACCENT_HOVER = "#111827", "#6b7280", "#2563eb", "#1d4ed8"
FONT = "Leelawadee UI"  # ฟอนต์ไทยของ Windows ที่ดูทันสมัย, ถ้าไม่มี Tk จะใช้ฟอนต์อื่นแทนเอง


POLISH_MODEL = "gpt-4o-mini"
# shortcut: ราคา USD ต่อ 1 ล้าน token (input, output) ณ ตอนเขียน, แก้ตรงนี้ถ้าเปลี่ยนโมเดล/ราคา
POLISH_PRICE = (0.15, 0.60)
POLISH_CHUNK = 6000  # ตัวอักษรต่อคำขอ กันผลลัพธ์ยาวเกินลิมิต output ของโมเดล
POLISH_PROMPT = ("คุณคือบรรณาธิการ ปรับข้อความที่ถอดจากเสียงให้อ่านง่าย: แก้คำผิดจากการถอดเสียง ตัดคำซ้ำ/คำติดปาก "
                 "เว้นวรรคและแบ่งย่อหน้าให้เหมาะสม ใส่เครื่องหมายวรรคตอน ห้ามเปลี่ยนความหมาย ห้ามสรุปหรือตัดเนื้อหา "
                 "ตอบเป็นภาษาเดียวกับต้นฉบับ บรรทัดที่ขึ้นต้นด้วย ■ ให้คงไว้ตามเดิม ตอบเฉพาะข้อความที่ปรับแล้ว")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # ไม่ให้หน้าต่าง cmd เด้งในตัวติดตั้ง


def ffmpeg_exe():
    try:  # ffmpeg ที่มากับแพ็กเกจ imageio-ffmpeg ผู้ใช้ไม่ต้องติดตั้งเอง
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


FFMPEG = ffmpeg_exe()


def duration_sec(path):
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out.stderr)
    if not m:
        raise RuntimeError(f"อ่านความยาวไฟล์ไม่ได้: {path}\n{out.stderr.strip()[-500:]}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def split_text(text, size=POLISH_CHUNK):
    """ตัดข้อความเป็นก้อนไม่เกิน size ตัวอักษร โดยตัดที่ขึ้นบรรทัดใหม่เมื่อทำได้"""
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:  # บรรทัดยาวมากไม่มีขึ้นบรรทัด ต้องตัดกลางบรรทัด
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(line[:size]); line = line[size:]
        if len(cur) + len(line) > size:
            chunks.append(cur); cur = ""
        cur += line
    if cur.strip():
        chunks.append(cur)
    return chunks


def polish_estimate_usd(text):
    # shortcut: เดา token ≈ ตัวอักษร/2 (ภาษาไทย) เป็นค่าประมาณก่อนส่ง, ค่าจริงคิดจาก usage ที่ API ตอบกลับ
    tok = len(text) / 2 + 200 * len(split_text(text))
    return tok * POLISH_PRICE[0] / 1e6 + len(text) / 2 * POLISH_PRICE[1] / 1e6


def polish(text, api_key, progress):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    out, usd, chunks = [], 0.0, split_text(text)
    for i, chunk in enumerate(chunks, 1):
        progress(f"กำลังปรับคำ {i}/{len(chunks)}")
        r = client.chat.completions.create(model=POLISH_MODEL, temperature=0.2, messages=[
            {"role": "system", "content": POLISH_PROMPT}, {"role": "user", "content": chunk}])
        out.append(r.choices[0].message.content.strip())
        usd += (r.usage.prompt_tokens * POLISH_PRICE[0] + r.usage.completion_tokens * POLISH_PRICE[1]) / 1e6
    return "\n\n".join(out), usd


def cost_usd(seconds, model):
    return seconds / 60 * PRICES[model]


def split_audio(path, outdir):
    """แยกเสียงเป็น mp3 mono 16kHz และตัดเป็นช่วง ๆ ละ CHUNK_SEC วินาที"""
    pattern = os.path.join(outdir, "part_%03d.mp3")
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                        "-b:a", "32k", "-f", "segment", "-segment_time", str(CHUNK_SEC), pattern],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg แยกเสียงไม่สำเร็จ:\n{r.stderr.strip()}")
    return sorted(glob.glob(os.path.join(outdir, "part_*.mp3")))


def transcribe(path, model, api_key, language, progress):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    texts = []
    with tempfile.TemporaryDirectory() as tmp:
        parts = split_audio(path, tmp)
        for i, part in enumerate(parts, 1):
            progress(f"กำลังถอดเสียง ช่วง {i}/{len(parts)}")
            with open(part, "rb") as f:
                kw = {"model": model, "file": f}
                if language:
                    kw["language"] = language
                texts.append(client.audio.transcriptions.create(**kw).text.strip())
    return "\n".join(texts)


def fmt_min(sec):
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


# ประวัติเก็บแบบ JSON ทีละบรรทัด (append อย่างเดียว ไฟล์เดิมไม่เสียถ้าแอปปิดกลางคัน)
HISTORY = os.path.join(os.path.expanduser("~"), ".vd_transcribe_history.jsonl")


def save_history(entry, path=HISTORY):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_history(path=HISTORY):
    if not os.path.exists(path):
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                items.append(json.loads(line))
            except ValueError:
                pass  # ข้ามบรรทัดที่เสีย ไม่ให้ทั้งประวัติพัง
    return items[::-1]  # ล่าสุดก่อน


class App(tk.Tk):
    def __init__(self):
        try:  # ให้ตัวหนังสือคมบนจอที่ตั้ง scaling 125-150% (Windows)
            import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        super().__init__()
        self.scale = self.winfo_fpixels("1i") / 96
        self.title("ถอดเสียงวิดีโอ")
        h = min(self.px(760), self.winfo_screenheight() - self.px(80))
        self.geometry(f"{self.px(920)}x{h}"); self.minsize(self.px(760), self.px(560))
        self.configure(bg=BG)
        self.files = {}  # path -> seconds
        self.style_widgets()

        root = ttk.Frame(self, padding=(28, 22)); root.pack(fill="both", expand=True)

        # หัวข้อ
        head = ttk.Frame(root); head.pack(fill="x")
        ttk.Label(head, text="ถอดเสียงวิดีโอ", style="H1.TLabel").pack(side="left")
        ttk.Button(head, text="ประวัติ", style="Secondary.TButton", command=self.show_history).pack(side="right")
        ttk.Label(root, text="แปลงเสียงจากวิดีโอหรือไฟล์เสียงเป็นข้อความด้วย OpenAI พร้อมดูค่าใช้จ่ายก่อนเริ่ม",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 16))

        # การ์ดตั้งค่า
        s = ttk.Frame(root, style="Card.TFrame", padding=16); s.pack(fill="x")
        s.columnconfigure(1, weight=1); s.columnconfigure(3, weight=1)
        self.key = tk.StringVar(value=os.environ.get("OPENAI_API_KEY", ""))
        self.model = tk.StringVar(value="gpt-4o-mini-transcribe")
        self.lang = tk.StringVar(value="th")
        self.rate = tk.StringVar(value="33")
        keyrow = ttk.Frame(s, style="Card.TFrame"); keyrow.columnconfigure(0, weight=1)
        ttk.Entry(keyrow, textvariable=self.key, show="•").grid(row=0, column=0, sticky="we")
        ttk.Button(keyrow, text="วาง", style="Secondary.TButton", command=self.paste_key).grid(row=0, column=1, padx=(8, 0))
        self.field(s, 0, 0, "OpenAI API Key", keyrow, span=3)
        cb = ttk.Combobox(s, textvariable=self.model, values=list(PRICES), state="readonly")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.field(s, 2, 0, "โมเดล", cb)
        self.field(s, 2, 2, "ภาษา (th, en หรือเว้นว่าง = อัตโนมัติ)", ttk.Entry(s, textvariable=self.lang))
        rate = ttk.Entry(s, textvariable=self.rate); rate.bind("<KeyRelease>", lambda e: self.refresh())
        self.field(s, 4, 0, "อัตราแลกเปลี่ยน (บาท/USD)", rate)

        # รายการไฟล์
        bar = ttk.Frame(root); bar.pack(fill="x", pady=(18, 8))
        ttk.Label(bar, text="ไฟล์", style="H2.TLabel").pack(side="left")
        ttk.Button(bar, text="ล้างทั้งหมด", style="Ghost.TButton", command=self.clear).pack(side="right")
        ttk.Button(bar, text="ลบที่เลือก", style="Ghost.TButton", command=self.remove).pack(side="right", padx=6)
        ttk.Button(bar, text="+  เพิ่มไฟล์", style="Secondary.TButton", command=self.add_files).pack(side="right")

        self.tree = ttk.Treeview(root, columns=("dur", "cost"), height=4, selectmode="extended")
        self.tree.heading("#0", text="ชื่อไฟล์", anchor="w")
        self.tree.heading("dur", text="ความยาว", anchor="e"); self.tree.heading("cost", text="ค่าใช้จ่าย", anchor="e")
        self.tree.column("#0", stretch=True); self.tree.column("dur", width=self.px(110), anchor="e", stretch=False)
        self.tree.column("cost", width=self.px(150), anchor="e", stretch=False)
        self.tree.pack(fill="x")
        self.tree.bind("<Delete>", lambda e: self.remove())

        # สรุปค่าใช้จ่าย + ปุ่มเริ่ม
        sum_ = ttk.Frame(root, style="Card.TFrame", padding=16); sum_.pack(fill="x", pady=(12, 0))
        left = ttk.Frame(sum_, style="Card.TFrame"); left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="ค่าใช้จ่ายโดยประมาณ", style="CardMuted.TLabel").pack(anchor="w")
        self.total = ttk.Label(left, text="—", style="Big.TLabel"); self.total.pack(anchor="w")
        self.detail = ttk.Label(left, text="เพิ่มไฟล์เพื่อดูราคา", style="CardMuted.TLabel"); self.detail.pack(anchor="w")
        self.go = ttk.Button(sum_, text="เริ่มถอดเสียง", style="Accent.TButton", command=self.start)
        self.go.pack(side="right")

        self.progress = ttk.Progressbar(root, mode="indeterminate", style="Thin.Horizontal.TProgressbar")
        self.status = ttk.Label(root, text="", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(10, 0))

        # ผลลัพธ์
        rbar = ttk.Frame(root); rbar.pack(fill="x", pady=(8, 8))
        ttk.Label(rbar, text="ผลลัพธ์", style="H2.TLabel").pack(side="left")
        ttk.Button(rbar, text="บันทึก .txt", style="Secondary.TButton", command=self.save).pack(side="right")
        ttk.Button(rbar, text="คัดลอก", style="Ghost.TButton", command=self.copy).pack(side="right", padx=6)
        self.polish_btn = ttk.Button(rbar, text="✨ ให้ GPT ปรับคำให้อ่านง่าย", style="Secondary.TButton",
                                     command=self.start_polish)
        self.polish_btn.pack(side="right")
        box = tk.Frame(root, bg=BORDER, padx=1, pady=1); box.pack(fill="both", expand=True)
        self.out = tk.Text(box, wrap="word", relief="flat", bg=BG, fg=TEXT, padx=14, pady=12,
                           font=(FONT, 11), insertbackground=TEXT, highlightthickness=0)
        self.out.pack(fill="both", expand=True)
        self.fix_shortcuts()
        self.refresh()

    def fix_shortcuts(self):
        # Tk ผูก Ctrl+V กับตัวอักษร v เลยใช้ไม่ได้ตอนแป้นพิมพ์เป็นภาษาไทย จึงผูกกับปุ่มจริง (keycode) แทน
        if sys.platform != "win32":
            return
        events = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "<<SelectAll>>", 90: "<<Undo>>"}

        def on_ctrl(e):
            ev = events.get(e.keycode)
            if ev:
                e.widget.event_generate(ev)
                return "break"
        for cls in ("TEntry", "Entry", "Text", "TCombobox"):
            self.bind_class(cls, "<Control-KeyPress>", on_ctrl, add="+")

    def paste_key(self):
        try:
            self.key.set(self.clipboard_get().strip())
        except tk.TclError:
            messagebox.showinfo("แจ้งเตือน", "คลิปบอร์ดว่าง ให้คัดลอก API Key มาก่อน")

    def px(self, n):
        return int(n * self.scale)

    def style_widgets(self):
        st = ttk.Style(self); st.theme_use("clam")
        base = (FONT, 10)
        st.configure(".", background=BG, foreground=TEXT, font=base, bordercolor=BORDER)
        st.configure("Card.TFrame", background=CARD)
        st.configure("H1.TLabel", font=(FONT, 20, "bold"))
        st.configure("H2.TLabel", font=(FONT, 12, "bold"))
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("CardMuted.TLabel", foreground=MUTED, background=CARD)
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("Big.TLabel", background=CARD, font=(FONT, 22, "bold"), foreground=TEXT)
        for name, bg, fg, hover in (("Accent", ACCENT, "#ffffff", ACCENT_HOVER),
                                    ("Secondary", "#eef2ff", ACCENT, "#e0e7ff"),
                                    ("Ghost", BG, MUTED, CARD)):
            st.configure(f"{name}.TButton", background=bg, foreground=fg, borderwidth=0, focusthickness=0,
                         padding=(18, 10) if name == "Accent" else (12, 6),
                         font=(FONT, 11, "bold") if name == "Accent" else base)
            st.map(f"{name}.TButton", background=[("disabled", BORDER), ("active", hover)],
                   foreground=[("disabled", MUTED)])
        st.configure("TEntry", fieldbackground=BG, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=7)
        st.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])
        st.configure("TCombobox", fieldbackground=BG, background=BG, bordercolor=BORDER, lightcolor=BORDER,
                     darkcolor=BORDER, arrowcolor=MUTED, padding=6)
        st.map("TCombobox", fieldbackground=[("readonly", BG)], bordercolor=[("focus", ACCENT)])
        st.configure("Treeview", background=BG, fieldbackground=BG, rowheight=self.px(34), borderwidth=1,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        st.configure("Treeview.Heading", background=CARD, foreground=MUTED, relief="flat", padding=(8, 6))
        st.map("Treeview", background=[("selected", "#eef2ff")], foreground=[("selected", TEXT)])
        st.map("Treeview.Heading", background=[("active", CARD)])
        st.configure("Thin.Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT, thickness=4,
                     bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT)

    def field(self, parent, row, col, label, widget, span=1):
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=col, columnspan=span,
                                                                     sticky="w", padx=(0 if col == 0 else 16, 0))
        widget.grid(row=row + 1, column=col, columnspan=span + 1 if span > 1 else 2, sticky="we",
                    padx=(0 if col == 0 else 16, 0), pady=(4, 12))

    def thb(self, usd):
        try:
            return f"{usd * float(self.rate.get()):,.2f} บาท"
        except ValueError:
            return f"${usd:.4f}"

    def refresh(self):
        model = self.model.get()
        for p in self.tree.get_children():
            self.tree.set(p, "cost", f"{self.thb(cost_usd(self.files[p], model))}")
        if not self.files:
            self.total.config(text="—"); self.detail.config(text="เพิ่มไฟล์เพื่อดูราคา")
            return
        total = sum(self.files.values()); usd = cost_usd(total, model)
        self.total.config(text=self.thb(usd))
        self.detail.config(text=f"{len(self.files)} ไฟล์ · {total / 60:.1f} นาที · ${usd:.4f} "
                                f"(${PRICES[model]}/นาที)  ค่าจริงดูที่ OpenAI dashboard")

    def add_files(self):
        for p in filedialog.askopenfilenames(filetypes=[("วิดีโอ/เสียง", MEDIA), ("ทั้งหมด", "*.*")]):
            if p in self.files:
                continue
            try:
                self.files[p] = duration_sec(p)
            except Exception as ex:
                messagebox.showerror("ผิดพลาด", str(ex)); continue
            self.tree.insert("", "end", iid=p, text="  " + os.path.basename(p), values=(fmt_min(self.files[p]), ""))
        self.refresh()

    def remove(self):
        for p in self.tree.selection():
            self.tree.delete(p); self.files.pop(p, None)
        self.refresh()

    def clear(self):
        self.files.clear(); self.tree.delete(*self.tree.get_children()); self.refresh()

    def set_status(self, msg):
        self.after(0, lambda: self.status.config(text=msg))

    def start(self):
        if not self.files:
            return messagebox.showinfo("แจ้งเตือน", "กรุณาเพิ่มไฟล์ก่อน")
        if not self.key.get().strip():
            return messagebox.showinfo("แจ้งเตือน", "กรุณาใส่ OpenAI API Key")
        self.refresh()
        if not messagebox.askyesno("ยืนยันค่าใช้จ่าย",
                                   f"ค่าใช้จ่ายโดยประมาณ {self.total.cget('text')}\n{self.detail.cget('text')}"
                                   "\n\nเริ่มถอดเสียงเลยไหม?"):
            return
        self.go.config(state="disabled", text="กำลังทำงาน...")
        self.progress.pack(fill="x", pady=(10, 0), before=self.status); self.progress.start(12)
        args = (list(self.files), self.model.get(), self.key.get().strip(), self.lang.get().strip())
        threading.Thread(target=self.run, args=args, daemon=True).start()

    def run(self, paths, model, key, lang):
        for p in paths:
            name = os.path.basename(p)
            try:
                text = transcribe(p, model, key, lang, lambda m: self.set_status(f"{name} — {m}"))
                block = f"■ {name}\n{text}\n\n"
                save_history({"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "file": p,
                              "model": model, "seconds": self.files.get(p, 0),
                              "usd": cost_usd(self.files.get(p, 0), model), "text": text})
            except Exception as ex:
                block = f"■ {name} (ผิดพลาด)\n{ex}\n\n"
            self.after(0, lambda b=block: (self.out.insert("end", b), self.out.see("end")))
        self.set_status("✓ เสร็จแล้ว")
        self.after(0, self.done)

    def done(self):
        self.progress.stop(); self.progress.pack_forget()
        self.go.config(state="normal", text="เริ่มถอดเสียง")

    def show_history(self):
        items = load_history()
        win = tk.Toplevel(self, bg=BG); win.title("ประวัติการถอดเสียง")
        win.geometry(f"{self.px(960)}x{min(self.px(620), self.winfo_screenheight() - self.px(80))}")
        frame = ttk.Frame(win, padding=(24, 18)); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ประวัติการถอดเสียง", style="H1.TLabel").pack(anchor="w")
        total = sum(i.get("usd", 0) for i in items)
        ttk.Label(frame, text=f"{len(items)} รายการ · ใช้ไปทั้งหมดประมาณ {self.thb(total)} (${total:.4f})",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 12))
        tree = ttk.Treeview(frame, columns=("time", "model", "dur", "cost"), height=7, selectmode="browse")
        for col, txt, w in (("#0", "ชื่อไฟล์", 0), ("time", "วันที่", 150), ("model", "โมเดล", 210),
                            ("dur", "ความยาว", 90), ("cost", "ค่าใช้จ่าย", 120)):
            tree.heading(col, text=txt, anchor="w")
            if w:
                tree.column(col, width=self.px(w), stretch=False)
        for n, it in enumerate(items):
            tree.insert("", "end", iid=str(n), text="  " + os.path.basename(it.get("file", "")),
                        values=(it.get("time", ""), it.get("model", ""), fmt_min(it.get("seconds", 0)),
                                self.thb(it.get("usd", 0))))
        tree.pack(fill="x")
        bar = ttk.Frame(frame); bar.pack(fill="x", pady=(10, 8))
        preview = tk.Text(frame, wrap="word", relief="flat", bg=CARD, fg=TEXT, padx=14, pady=12,
                          font=(FONT, 11), highlightthickness=0)

        def selected():
            sel = tree.selection()
            return items[int(sel[0])] if sel else None

        def on_select(_=None):
            it = selected(); preview.delete("1.0", "end")
            if it:
                preview.insert("1.0", it.get("text", ""))

        def open_in_main():
            it = selected()
            if it:
                self.out.delete("1.0", "end")
                self.out.insert("1.0", f"■ {os.path.basename(it['file'])}\n{it['text']}\n")
                win.destroy()

        tree.bind("<<TreeviewSelect>>", on_select); tree.bind("<Double-1>", lambda e: open_in_main())
        ttk.Label(bar, text="ดับเบิลคลิกเพื่อเปิดในหน้าหลัก", style="Muted.TLabel").pack(side="left")
        ttk.Button(bar, text="เปิดในหน้าหลัก", style="Secondary.TButton", command=open_in_main).pack(side="right")
        preview.pack(fill="both", expand=True)
        if not items:
            preview.insert("1.0", "ยังไม่มีประวัติ ถอดเสียงไฟล์แรกแล้วจะแสดงที่นี่")

    def start_polish(self):
        text = self.out.get("1.0", "end").strip()
        if not text:
            return messagebox.showinfo("แจ้งเตือน", "ยังไม่มีข้อความให้ปรับ ถอดเสียงหรือเปิดจากประวัติก่อน")
        if not self.key.get().strip():
            return messagebox.showinfo("แจ้งเตือน", "กรุณาใส่ OpenAI API Key")
        est = polish_estimate_usd(text)
        if not messagebox.askyesno("ยืนยันค่าใช้จ่าย",
                                   f"ปรับคำด้วย {POLISH_MODEL}\nข้อความ {len(text):,} ตัวอักษร\n"
                                   f"ค่าใช้จ่ายโดยประมาณ {self.thb(est)} (${est:.4f})\n\nเริ่มเลยไหม?"):
            return
        self.polish_btn.config(state="disabled"); self.go.config(state="disabled")
        self.progress.pack(fill="x", pady=(10, 0), before=self.status); self.progress.start(12)
        threading.Thread(target=self.run_polish, args=(text, self.key.get().strip()), daemon=True).start()

    def run_polish(self, text, key):
        try:
            result, usd = polish(text, key, self.set_status)
            save_history({"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                          "file": "ข้อความที่ปรับคำแล้ว", "model": POLISH_MODEL, "seconds": 0,
                          "usd": usd, "text": result})

            def show():
                self.out.delete("1.0", "end"); self.out.insert("1.0", result)
                self.status.config(text=f"✓ ปรับคำเสร็จแล้ว ค่าใช้จ่ายจริง {self.thb(usd)} (${usd:.4f}) "
                                        "ต้นฉบับยังอยู่ในประวัติ")
            self.after(0, show)
        except Exception as ex:
            self.after(0, lambda: messagebox.showerror("ผิดพลาด", str(ex)))
            self.set_status("ปรับคำไม่สำเร็จ")
        self.after(0, lambda: (self.done(), self.polish_btn.config(state="normal")))

    def copy(self):
        self.clipboard_clear(); self.clipboard_append(self.out.get("1.0", "end").strip())
        self.set_status("คัดลอกแล้ว")

    def save(self):
        p = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.out.get("1.0", "end"))
            self.set_status(f"บันทึกแล้ว: {p}")


def selftest():
    assert abs(cost_usd(600, "gpt-4o-transcribe") - 0.06) < 1e-9
    assert abs(cost_usd(90, "gpt-4o-mini-transcribe") - 0.0045) < 1e-9
    assert fmt_min(125) == "2:05"
    t = "ก" * 50 + "\n" + "ข" * 13000 + "\n" + "ค" * 10
    parts = split_text(t, 6000)
    assert "".join(parts) == t and all(len(p) <= 6000 for p in parts), [len(p) for p in parts]
    assert split_text("") == [] and polish_estimate_usd("ก" * 2000) > 0
    with tempfile.TemporaryDirectory() as tmp:
        h = os.path.join(tmp, "h.jsonl")
        save_history({"file": "a.mp4", "text": "หนึ่ง"}, h); save_history({"file": "b.mp4", "text": "สอง"}, h)
        with open(h, "a", encoding="utf-8") as f:
            f.write("{broken\n")
        assert [i["text"] for i in load_history(h)] == ["สอง", "หนึ่ง"]
        assert load_history(os.path.join(tmp, "none.jsonl")) == []
        src = os.path.join(tmp, "t.mp4")  # วิดีโอทดสอบ 25 นาที มีเสียง
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=d=1500", "-f", "lavfi",
                        "-i", "color=c=black:s=64x64:d=1500:r=1", "-shortest", src], check=True)
        assert abs(duration_sec(src) - 1500) < 2
        out = os.path.join(tmp, "o"); os.mkdir(out)
        parts = split_audio(src, out)
        assert len(parts) == 3, parts
        assert all(os.path.getsize(x) < 25 * 1024 * 1024 for x in parts)
    print("selftest OK")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else App().mainloop()
