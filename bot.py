import os
import logging
import aiosqlite
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)

logging.basicConfig(level=logging.INFO)

TOKEN     = os.environ.get("TELEGRAM_TOKEN", "ISI_TOKEN_BARU_DI_SINI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1TqrkExfVGrY6SpVqWjjNuHPlU7b3lCKJ5tazbaTg1_4/edit?usp=sharing")
DB    = os.environ.get("DB_PATH", "/data/keuangan.db")

# Map Telegram user ID -> nama tab di sheet
PENGGUNA = {
    5527441506: "Ahmad Zaeni",
    5332413035: "Melia Efi",
}

PILIH_TIPE, PILIH_KATEGORI, ISI_NOMINAL, ISI_CATATAN = range(4)
PILIH_UNTUK_SIAPA = 10
KONFIRMASI_RESET = 11
TUNGGU_ID_HAPUS = 12
KONFIRMASI_HAPUS = 13

KATEGORI = {
    "pemasukan":   [" Gaji", " Freelance", " Hadiah", " Lainnya"],
    "pengeluaran": [" Makan", " Transport", " Belanja",
                    "💊 Kesehatan", "📚 Pendidikan", "🏠 Rumah", "📦 Lainnya"],
}

def get_credentials_dict():
    import json, os, base64
    b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
    if b64:
        return json.loads(base64.b64decode(b64).decode("utf-8"))
    with open("credentials.json") as f:
        return json.load(f)

def get_service_email():
    return get_credentials_dict()["client_email"]

def get_gspread():
    from google.oauth2.service_account import Credentials as SACredentials
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds  = SACredentials.from_service_account_info(get_credentials_dict(), scopes=scopes)
    return gspread.authorize(creds)
    return gspread.authorize(creds)

def init_sheet(sh):
    # 1 tab per orang (nilai di PENGGUNA), bukan 1 tab "Transaksi" tunggal
    for nama_tab in set(PENGGUNA.values()):
        try:
            ws = sh.worksheet(nama_tab)
        except Exception:
            ws = sh.add_worksheet(nama_tab, rows=200, cols=6)
        if not ws.cell(1, 1).value:
            ws.update([["ID","Tanggal","Tipe","Kategori","Jumlah","Catatan"]], "A1:F1")
            ws.format("A1:F1", {
                "textFormat": {"bold": True, "foregroundColor": {"red":1,"green":1,"blue":1}},
                "backgroundColor": {"red":0.13,"green":0.59,"blue":0.33}
            })
    try:
        sh.worksheet("Dashboard")
    except Exception:
        db = sh.add_worksheet("Dashboard", rows=50, cols=5)
        db.update([["📊 BendaharaKu Dashboard", ""]], "A1:B1")
        db.update([["💚 Pemasukan Bulan Ini", "Rp 0"]])
        db.update([["💸 Pengeluaran Bulan Ini", "Rp 0"]])
        db.update([["✅ Saldo", "Rp 0"]])
        db.format("A1:B1", {"textFormat": {"bold": True, "fontSize": 14}})

def tambah_transaksi_sheet(client, row_id, nama_tab, tipe, kategori, nominal, catatan):
    sh = client.open_by_url(SHEET_URL)
    ws = sh.worksheet(nama_tab)
    tanggal = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws.append_row([row_id, tanggal, tipe.capitalize(), kategori, nominal, catatan or "-"])
    update_dashboard(sh)

def update_dashboard(sh):
    try:
        db    = sh.worksheet("Dashboard")
        bulan = datetime.now().strftime("%Y-%m")
        masuk, keluar = 0.0, 0.0
        kat_dict  = {}
        per_orang = {}
        for nama_tab in set(PENGGUNA.values()):
            ws = sh.worksheet(nama_tab)
            records = ws.get_all_records()
            for r in records:
                if not str(r["Tanggal"]).startswith(bulan):
                    continue
                jumlah = float(r["Jumlah"] or 0)
                if r["Tipe"] == "Pemasukan":
                    masuk += jumlah
                    per_orang[nama_tab] = per_orang.get(nama_tab, 0) + jumlah
                else:
                    keluar += jumlah
                    kat_dict[r["Kategori"]] = kat_dict.get(r["Kategori"], 0) + jumlah
                    per_orang[nama_tab] = per_orang.get(nama_tab, 0) - jumlah
        saldo = masuk - keluar
        db.update([["💚 Pemasukan Bulan Ini",  f"Rp {masuk:,.0f}"]])
        db.update([["💸 Pengeluaran Bulan Ini", f"Rp {keluar:,.0f}"]])
        db.update([["✅ Saldo",                 f"Rp {saldo:,.0f}"]])
        db.update([["📂 Per Kategori", "Total"]])
        row = 8
        for kat, total in sorted(kat_dict.items(), key=lambda x: -x[1]):
            db.update([[kat, f"Rp {total:,.0f}"]], f"A{row}:B{row}")
            row += 1
        row += 1
        db.update([["👤 Kontribusi Bersih", ""]], f"A{row}:B{row}")
        row += 1
        for nama_tab, total in per_orang.items():
            db.update([[nama_tab, f"Rp {total:,.0f}"]], f"A{row}:B{row}")
            row += 1
    except Exception as e:
        logging.error(f"Dashboard error: {e}")

# ── Database ──────────────────────────────────────────────
def run_db():
    import asyncio
    async def _init():
        async with aiosqlite.connect(DB) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS transaksi (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   INTEGER,
                    tipe      TEXT,
                    kategori  TEXT,
                    nominal   REAL,
                    catatan   TEXT,
                    tanggal   TEXT
                )
            """)
            await db.commit()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_init())

async def simpan_transaksi(user_id, tipe, kategori, nominal, catatan):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "INSERT INTO transaksi (user_id,tipe,kategori,nominal,catatan,tanggal) VALUES (?,?,?,?,?,?)",
            (user_id, tipe, kategori, nominal, catatan, datetime.now().strftime("%Y-%m-%d"))
        )
        await db.commit()
        return cursor.lastrowid

# ── Handlers ──────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in PENGGUNA:
        await update.message.reply_text(
            "🚫 Bot ini privat, kamu belum terdaftar di PENGGUNA.\n"
            f"User ID kamu: `{user.id}` — tambahin ke bot.py kalau ini memang kamu.",
            parse_mode="Markdown"
        )
        return
    nama_tab = PENGGUNA[user.id]
    await update.message.reply_text(
        f"👋 *Halo {user.first_name}!* Kamu tercatat sebagai *{nama_tab}*.\n\n"
        f"📊 [Buka Google Sheets]({SHEET_URL})\n\n"
        f"Perintah:\n"
        f"📝 /catat — Catat transaksi\n"
        f"/catatuntuk — Catat untuk pasangan\n"
        f"📊 /laporan — Laporan bulan ini\n"
        f"🔗 /sheet — Link Google Sheets",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

async def sheet_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🔗 Google Sheets:\n{SHEET_URL}")


async def catatuntuk_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in PENGGUNA:
        await update.message.reply_text("🚫 Kamu belum terdaftar.")
        return ConversationHandler.END
    kb = [["🙋 Diri sendiri"]]
    label_map = {"🙋 Diri sendiri": uid}
    for other_id, other_nama in PENGGUNA.items():
        if other_id != uid:
            btn = f"💕 {other_nama}"
            kb.append([btn])
            label_map[btn] = other_id
    ctx.user_data["label_map"] = label_map
    await update.message.reply_text(
        "Catat transaksi ini untuk siapa?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return PILIH_UNTUK_SIAPA

async def pilih_untuk_siapa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    teks = update.message.text
    label_map = ctx.user_data.get("label_map", {})
    target_id = label_map.get(teks)
    if target_id is None:
        await update.message.reply_text("❌ Pilih dari tombol yang tersedia ya.")
        return PILIH_UNTUK_SIAPA
    ctx.user_data["target_id"] = target_id
    ctx.user_data["nama_tab"] = PENGGUNA[target_id]
    kb = [["💚 Pemasukan", "💸 Pengeluaran"]]
    await update.message.reply_text(
        f"Jenis transaksi untuk {PENGGUNA[target_id]}:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return PILIH_TIPE
async def catat_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in PENGGUNA:
        await update.message.reply_text("❌ Kamu belum terdaftar di bot ini.")
        return ConversationHandler.END
    kb = [["💚 Pemasukan", "💸 Pengeluaran"]]
    await update.message.reply_text(
        "Jenis transaksi:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return PILIH_TIPE

async def pilih_tipe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    teks = update.message.text
    if "Selesai" in teks:
        await update.message.reply_text("✅ Sesi pencatatan selesai!", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    if "Pemasukan" in teks:
        ctx.user_data["tipe"] = "pemasukan"
        cats = KATEGORI["pemasukan"]
    else:
        ctx.user_data["tipe"] = "pengeluaran"
        cats = KATEGORI["pengeluaran"]
    kb = [[c] for c in cats]
    await update.message.reply_text(
        "Pilih kategori:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return PILIH_KATEGORI

async def pilih_kategori(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["kategori"] = update.message.text
    await update.message.reply_text("💵 Masukkan nominal (angka saja):")
    return ISI_NOMINAL

async def isi_nominal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["nominal"] = float(update.message.text.replace(",", "").replace(".", ""))
        await update.message.reply_text("📝 Tambahkan catatan (atau ketik '-' untuk skip):")
        return ISI_CATATAN
    except ValueError:
        await update.message.reply_text("❌ Nominal tidak valid, masukkan angka saja:")
        return ISI_NOMINAL

async def isi_catatan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    catatan  = update.message.text if update.message.text != "-" else ""
    d        = ctx.user_data
    user     = update.effective_user
    nama_tab = PENGGUNA[user.id]

    row_id = await simpan_transaksi(user.id, d["tipe"], d["kategori"], d["nominal"], catatan)

    try:
        client = get_gspread()
        tambah_transaksi_sheet(client, row_id, nama_tab, d["tipe"], d["kategori"], d["nominal"], catatan)
    except Exception as e:
        logging.error(f"Sheets sync error: {e}")

    # Hitung saldo terkini dari DB
    uid = user.id
    bulan = datetime.now().strftime("%Y-%m")
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(nominal),0) FROM transaksi WHERE user_id=? AND tipe='pemasukan'", (uid,))
        total_masuk = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT COALESCE(SUM(nominal),0) FROM transaksi WHERE user_id=? AND tipe='pengeluaran'", (uid,))
        total_keluar = (await cur.fetchone())[0]
    saldo = total_masuk - total_keluar
    emoji = "💚" if d["tipe"] == "pemasukan" else "💸"
    emoji_saldo = "✅" if saldo >= 0 else "⚠️"
    kb = [["💚 Pemasukan", "💸 Pengeluaran"], ["🏁 Selesai"]]
    await update.message.reply_text(
        f"{emoji} *Transaksi disimpan!*\n\n"
        f"Tipe     : {d['tipe'].capitalize()}\n"
        f"Kategori : {d['kategori']}\n"
        f"Nominal  : Rp {d['nominal']:,.0f}\n"
        f"Catatan  : {catatan or '-'}\n\n"
        f"{emoji_saldo} *Saldo saat ini: Rp {saldo:,.0f}*\n\n"
        f"Mau catat lagi atau selesai?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return PILIH_TIPE

async def reset_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in PENGGUNA:
        await update.message.reply_text("❌ Kamu belum terdaftar di bot ini.")
        return ConversationHandler.END
    kb = [["✅ Ya, reset semua"], ["❌ Batal"]]
    await update.message.reply_text(
        "⚠️ *Reset Transaksi*\n\nSemua transaksi di tab kamu akan dihapus dari database lokal dan Google Sheets.\n\nYakin?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return KONFIRMASI_RESET

async def reset_konfirmasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    teks = update.message.text
    if "Ya" not in teks:
        await update.message.reply_text("❌ Reset dibatalkan.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    uid = update.effective_user.id
    nama_tab = PENGGUNA[uid]
    # Hapus DB lokal (cuma transaksi milik user ini)
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM transaksi WHERE user_id=?", (uid,))
        await db.commit()
    # Reset tab sheet milik user ini
    try:
        client = get_gspread()
        sh = client.open_by_url(SHEET_URL)
        ws = sh.worksheet(nama_tab)
        ws.clear()
        ws.update([["ID","Tanggal","Tipe","Kategori","Jumlah","Catatan"]], "A1:F1")
        ws.format("A1:F1", {
            "textFormat": {"bold": True, "foregroundColor": {"red":1,"green":1,"blue":1}},
            "backgroundColor": {"red":0.13,"green":0.59,"blue":0.33}
        })
        update_dashboard(sh)
    except Exception as e:
        logging.error(f"Reset sheet error: {e}")
    await update.message.reply_text(f"✅ Transaksi tab \"{nama_tab}\" berhasil direset!", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def hapus_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in PENGGUNA:
        await update.message.reply_text("❌ Kamu belum terdaftar di bot ini.")
        return ConversationHandler.END
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id,tipe,kategori,nominal,catatan,tanggal FROM transaksi WHERE user_id=? ORDER BY id DESC LIMIT 5",
            (uid,))
        rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("❌ Belum ada transaksi.")
        return ConversationHandler.END
    teks = "🗑️ *Pilih ID transaksi yang mau dihapus:*\n\n"
    for row in rows:
        tid, tipe, kat, nom, cat, tgl = row
        icon = "💚" if tipe == "pemasukan" else "💸"
        teks += f"ID `{tid}` — {icon} {kat} Rp {nom:,.0f} ({tgl})\n"
    teks += "\nKetik ID transaksi:"
    await update.message.reply_text(teks, parse_mode="Markdown")
    return TUNGGU_ID_HAPUS

async def hapus_terima_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        tid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID tidak valid, masukkan angka saja:")
        return TUNGGU_ID_HAPUS
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT id,tipe,kategori,nominal,tanggal FROM transaksi WHERE id=? AND user_id=?", (tid, uid))
        row = await cur.fetchone()
    if not row:
        await update.message.reply_text("❌ ID tidak ditemukan. Coba lagi:")
        return TUNGGU_ID_HAPUS
    ctx.user_data["hapus_id"] = tid
    tid, tipe, kat, nom, tgl = row
    icon = "💚" if tipe == "pemasukan" else "💸"
    kb = [["✅ Ya, hapus"], ["❌ Batal"]]
    await update.message.reply_text(
        f"Yakin hapus transaksi ini?\n\n"
        f"ID     : `{tid}`\n"
        f"Tipe   : {icon} {tipe.capitalize()}\n"
        f"Kategori: {kat}\n"
        f"Nominal : Rp {nom:,.0f}\n"
        f"Tanggal : {tgl}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return KONFIRMASI_HAPUS

async def hapus_konfirmasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if "Ya" not in update.message.text:
        await update.message.reply_text("❌ Dibatalkan.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    uid = update.effective_user.id
    tid = ctx.user_data.get("hapus_id")
    nama_tab = PENGGUNA[uid]
    # Hapus dari DB
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM transaksi WHERE id=? AND user_id=?", (tid, uid))
        await db.commit()
    # Hapus dari Sheet — cari row by ID kolom A di tab milik user ini
    try:
        client = get_gspread()
        sh = client.open_by_url(SHEET_URL)
        ws = sh.worksheet(nama_tab)
        cell = ws.find(str(tid))
        if cell and cell.col == 1:
            ws.delete_rows(cell.row)
        update_dashboard(sh)
    except Exception as e:
        logging.error(f"Hapus sheet error: {e}")
    await update.message.reply_text(f"✅ Transaksi ID {tid} berhasil dihapus!", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def batal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Dibatalkan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    if uid not in PENGGUNA:
        await update.message.reply_text("❌ Kamu belum terdaftar di bot ini.")
        return
    bulan = datetime.now().strftime("%Y-%m")
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(nominal),0) FROM transaksi WHERE user_id=? AND tipe='pemasukan' AND tanggal LIKE ?",
            (uid, f"{bulan}%"))
        masuk = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT COALESCE(SUM(nominal),0) FROM transaksi WHERE user_id=? AND tipe='pengeluaran' AND tanggal LIKE ?",
            (uid, f"{bulan}%"))
        keluar = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT kategori, SUM(nominal) FROM transaksi WHERE user_id=? AND tipe='pengeluaran' AND tanggal LIKE ? GROUP BY kategori ORDER BY SUM(nominal) DESC",
            (uid, f"{bulan}%"))
        per_kat = await cur.fetchall()
        cur = await db.execute(
            "SELECT tipe,kategori,nominal,catatan,tanggal FROM transaksi WHERE user_id=? ORDER BY id DESC LIMIT 5",
            (uid,))
        terakhir = await cur.fetchall()
    saldo = masuk - keluar
    emoji_saldo = "✅" if saldo >= 0 else "⚠️"
    teks = (
        f"📊 *Laporan Bulan {bulan}*\n\n"
        f"💚 Pemasukan  : Rp {masuk:,.0f}\n"
        f"💸 Pengeluaran: Rp {keluar:,.0f}\n"
        f"{emoji_saldo} Saldo       : Rp {saldo:,.0f}\n"
    )
    if per_kat:
        teks += "\n📂 *Pengeluaran per Kategori:*\n"
        for kat, total in per_kat:
            teks += f"  {kat}: Rp {total:,.0f}\n"
    if terakhir:
        teks += "\n🕐 *5 Transaksi Terakhir:*\n"
        for tipe, kat, nom, cat, tgl in terakhir:
            icon = "💚" if tipe == "pemasukan" else "💸"
            teks += f"  {icon} {kat} — Rp {nom:,.0f} ({tgl})\n"
    teks += f"\n📊 [Lihat Dashboard Lengkap]({SHEET_URL})"
    await update.message.reply_text(teks, parse_mode="Markdown", disable_web_page_preview=True)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *Bantuan BendaharaKu*\n\n"
        "/start — Daftar & mulai\n"
        "/catat — Catat pemasukan/pengeluaran\n"
        "/laporan — Laporan bulan ini\n"
        "/sheet — Link Google Sheets kamu\n"
        "/hapus — Hapus transaksi by ID\n"
        "/reset — Reset semua transaksi\n"
        "/batal — Batalkan input",
        parse_mode="Markdown"
    )

if __name__ == "__main__":
    run_db()
    print("✅ Database siap!")
    print(f"📧 Service account email: {get_service_email()}")

    try:
        client = get_gspread()
        sh = client.open_by_url(SHEET_URL)
        init_sheet(sh)
        print(f"✅ Sheet siap dengan tab: {', '.join(set(PENGGUNA.values()))}")
    except Exception as e:
        print(f"⚠️ Gagal inisialisasi sheet: {e}")

    app = ApplicationBuilder().token(TOKEN).build()

    catat_conv = ConversationHandler(
        entry_points=[CommandHandler("catat", catat_start)],
        states={
            PILIH_TIPE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, pilih_tipe)],
            PILIH_KATEGORI: [MessageHandler(filters.TEXT & ~filters.COMMAND, pilih_kategori)],
            ISI_NOMINAL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, isi_nominal)],
            ISI_CATATAN:    [MessageHandler(filters.TEXT & ~filters.COMMAND, isi_catatan)],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )

    reset_conv = ConversationHandler(
        entry_points=[CommandHandler("reset", reset_start)],
        states={
            KONFIRMASI_RESET: [MessageHandler(filters.TEXT & ~filters.COMMAND, reset_konfirmasi)],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )

    hapus_conv = ConversationHandler(
        entry_points=[CommandHandler("hapus", hapus_start)],
        states={
            TUNGGU_ID_HAPUS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, hapus_terima_id)],
            KONFIRMASI_HAPUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, hapus_konfirmasi)],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(catat_conv)
    app.add_handler(reset_conv)
    app.add_handler(hapus_conv)
    app.add_handler(CommandHandler("laporan", laporan))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("sheet", sheet_link))
    print("🤖 Bot berjalan... tekan Ctrl+C untuk stop")

    catatuntuk_conv = ConversationHandler(
        entry_points=[CommandHandler("catatuntuk", catatuntuk_start)],
        states={
            PILIH_UNTUK_SIAPA: [MessageHandler(filters.TEXT & ~filters.COMMAND, pilih_untuk_siapa)],
            PILIH_TIPE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, pilih_tipe)],
            PILIH_KATEGORI:    [MessageHandler(filters.TEXT & ~filters.COMMAND, pilih_kategori)],
            ISI_NOMINAL:       [MessageHandler(filters.TEXT & ~filters.COMMAND, isi_nominal)],
            ISI_CATATAN:       [MessageHandler(filters.TEXT & ~filters.COMMAND, isi_catatan)],
        },
        fallbacks=[CommandHandler("batal", batal)],
    )
    app.add_handler(catatuntuk_conv)
    app.run_polling()
