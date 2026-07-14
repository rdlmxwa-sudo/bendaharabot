import os
import json
import base64
import logging
import traceback
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

# ── CONFIG ────────────────────────────────────────────────────────────────────
TOKEN     = os.environ.get("TELEGRAM_TOKEN", "ISI_TOKEN_DI_SINI")
SHEET_URL = os.environ.get("SHEET_URL",      "ISI_SHEET_URL_DI_SINI")

PENGGUNA = {
    5527441506: "Ahmad Zaeni",
    5332413035: "Melia Efi",
}

KATEGORI = {
    "pemasukan":   ["💼 Gaji", "💰 Freelance", "🎁 Hadiah", "📦 Lainnya"],
    "pengeluaran": ["🍔 Makan", "🚗 Transport", "🛍️ Belanja",
                    "💊 Kesehatan", "📚 Pendidikan", "🏠 Rumah", "📦 Lainnya"],
}

# ── STATE ─────────────────────────────────────────────────────────────────────
PILIH_TIPE, PILIH_KATEGORI, ISI_NOMINAL, ISI_CATATAN = range(4)
KONFIRMASI_RESET = 10
TUNGGU_ID_HAPUS  = 11
KONFIRMASI_HAPUS = 12

# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def get_gspread():
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    b64 = os.environ.get("GOOGLE_CREDENTIALS_B64")
    if b64:
        info = json.loads(base64.b64decode(b64).decode("utf-8"))
    else:
        with open("credentials.json") as f:
            info = json.load(f)
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet(nama_tab: str):
    client = get_gspread()
    sh     = client.open_by_url(SHEET_URL)
    return sh.worksheet(nama_tab)

def init_sheets():
    """Pastiin tab & header ada. Gak pernah hapus data yang udah ada."""
    client = get_gspread()
    sh     = client.open_by_url(SHEET_URL)
    header = ["No", "Tanggal", "Tipe", "Kategori", "Jumlah (Rp)", "Catatan"]
    for nama_tab in set(PENGGUNA.values()):
        try:
            ws = sh.worksheet(nama_tab)
        except Exception:
            ws = sh.add_worksheet(nama_tab, rows=500, cols=6)
        # Cek header — kalau baris 1 kosong baru isi, gak sentuh yang udah ada
        if not ws.cell(1, 1).value:
            ws.update([header], "A1:F1")
            ws.format("A1:F1", {
                "textFormat": {"bold": True,
                               "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "backgroundColor": {"red": 0.13, "green": 0.59, "blue": 0.33}
            })
    print(f"✅ Sheet siap: {', '.join(set(PENGGUNA.values()))}")

def tambah_baris(nama_tab: str, tipe: str, kategori: str, nominal: float, catatan: str):
    """Append 1 baris ke tab yang sesuai. Data lama tidak pernah disentuh."""
    ws      = get_sheet(nama_tab)
    # Nomor urut = jumlah baris yang udah ada (termasuk header) 
    no      = len(ws.get_all_values())
    tanggal = datetime.now().strftime("%d/%m/%Y %H:%M")
    jumlah  = nominal if tipe == "pemasukan" else -nominal
    ws.append_row([no, tanggal, tipe.capitalize(), kategori, jumlah, catatan or "-"])

def ambil_semua(nama_tab: str):
    ws = get_sheet(nama_tab)
    return ws.get_all_records()

def hapus_baris_by_no(nama_tab: str, no: int):
    """Hapus baris berdasarkan nilai kolom No."""
    ws    = get_sheet(nama_tab)
    cells = ws.findall(str(no))
    for cell in cells:
        if cell.col == 1:  # kolom No
            ws.delete_rows(cell.row)
            return True
    return False

# ── HELPERS ───────────────────────────────────────────────────────────────────
def cek_akses(uid: int, update):
    return uid in PENGGUNA

async def tolak(update: Update):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🚫 Bot ini privat.\nUser ID kamu: `{uid}`",
        parse_mode="Markdown"
    )

# ── COMMAND: /start ───────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in PENGGUNA:
        await tolak(update); return
    nama = PENGGUNA[uid]
    await update.message.reply_text(
        f"👋 Halo *{update.effective_user.first_name}!*\n"
        f"Kamu tercatat sebagai *{nama}*.\n\n"
        f"Perintah:\n"
        f"📝 /catat — Catat transaksi\n"
        f"📊 /laporan — Laporan bulan ini\n"
        f"🗑 /hapus — Hapus transaksi\n"
        f"🔄 /reset — Reset semua transaksi tab kamu\n"
        f"❌ /batal — Batalkan perintah aktif",
        parse_mode="Markdown"
    )

# ── COMMAND: /catat ───────────────────────────────────────────────────────────
async def catat_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in PENGGUNA:
        await tolak(update); return ConversationHandler.END
    kb = [["💚 Pemasukan", "💸 Pengeluaran"]]
    await update.message.reply_text(
        "Jenis transaksi:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return PILIH_TIPE

async def pilih_tipe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    teks = update.message.text
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
    await update.message.reply_text(
        "💵 Masukkan nominal (angka saja, contoh: 50000):",
        reply_markup=ReplyKeyboardRemove()
    )
    return ISI_NOMINAL

async def isi_nominal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    teks = update.message.text.replace(".", "").replace(",", "").strip()
    if not teks.isdigit():
        await update.message.reply_text("❌ Masukkan angka saja, contoh: 50000")
        return ISI_NOMINAL
    ctx.user_data["nominal"] = float(teks)
    await update.message.reply_text("📝 Catatan (atau ketik - kalau gak ada):")
    return ISI_CATATAN

async def isi_catatan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    catatan  = update.message.text if update.message.text != "-" else ""
    d        = ctx.user_data
    uid      = update.effective_user.id
    nama_tab = PENGGUNA[uid]
    tipe     = d["tipe"]
    kategori = d["kategori"]
    nominal  = d["nominal"]

    try:
        tambah_baris(nama_tab, tipe, kategori, nominal, catatan)
        emoji = "💚" if tipe == "pemasukan" else "💸"
        await update.message.reply_text(
            f"{emoji} *{tipe.capitalize()} berhasil dicatat!*\n\n"
            f"👤 Tab   : {nama_tab}\n"
            f"📂 Kategori: {kategori}\n"
            f"💵 Jumlah  : Rp {nominal:,.0f}\n"
            f"📝 Catatan : {catatan or '-'}",
            parse_mode="Markdown"
        )
    except Exception:
        traceback.print_exc()
        await update.message.reply_text("❌ Gagal menyimpan ke Sheet. Coba lagi.")

    return ConversationHandler.END

# ── COMMAND: /laporan ─────────────────────────────────────────────────────────
async def laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in PENGGUNA:
        await tolak(update); return
    nama_tab = PENGGUNA[uid]
    bulan    = datetime.now().strftime("%m/%Y")
    await update.message.reply_text("⏳ Mengambil data...")

    try:
        records  = ambil_semua(nama_tab)
        masuk    = 0.0
        keluar   = 0.0
        per_kat  = {}
        for r in records:
            tgl = str(r.get("Tanggal", ""))
            if not tgl.startswith(bulan[:2]):  # filter bulan
                continue
            # cek tahun juga
            if bulan[3:] not in tgl:
                continue
            jumlah = float(r.get("Jumlah (Rp)", 0) or 0)
            if jumlah >= 0:
                masuk += jumlah
            else:
                keluar += abs(jumlah)
                kat = r.get("Kategori", "Lainnya")
                per_kat[kat] = per_kat.get(kat, 0) + abs(jumlah)

        saldo       = masuk - keluar
        emoji_saldo = "✅" if saldo >= 0 else "⚠️"
        teks = (
            f"📊 *Laporan {nama_tab} — {bulan}*\n\n"
            f"💚 Pemasukan  : Rp {masuk:,.0f}\n"
            f"💸 Pengeluaran: Rp {keluar:,.0f}\n"
            f"{emoji_saldo} Saldo       : Rp {saldo:,.0f}\n"
        )
        if per_kat:
            teks += "\n📂 *Per Kategori:*\n"
            for kat, total in sorted(per_kat.items(), key=lambda x: -x[1]):
                teks += f"  {kat}: Rp {total:,.0f}\n"

        teks += f"\n📊 [Lihat Google Sheets]({SHEET_URL})"
        await update.message.reply_text(teks, parse_mode="Markdown",
                                        disable_web_page_preview=True)
    except Exception:
        traceback.print_exc()
        await update.message.reply_text("❌ Gagal mengambil data dari Sheet.")

# ── COMMAND: /hapus ───────────────────────────────────────────────────────────
async def hapus_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in PENGGUNA:
        await tolak(update); return ConversationHandler.END
    nama_tab = PENGGUNA[uid]

    try:
        records = ambil_semua(nama_tab)
        if not records:
            await update.message.reply_text("📭 Belum ada transaksi.")
            return ConversationHandler.END
        # Tampilkan 10 transaksi terakhir
        terakhir = records[-10:]
        teks = f"🗑 *Transaksi terakhir ({nama_tab}):*\n\n"
        for r in terakhir:
            jumlah = float(r.get("Jumlah (Rp)", 0) or 0)
            emoji  = "💚" if jumlah >= 0 else "💸"
            teks  += f"{emoji} No.{r['No']} | {r['Tanggal']} | {r['Kategori']} | Rp {abs(jumlah):,.0f}\n"
        teks += "\nKetik *No transaksi* yang mau dihapus:"
        await update.message.reply_text(teks, parse_mode="Markdown")
    except Exception:
        traceback.print_exc()
        await update.message.reply_text("❌ Gagal mengambil data.")
        return ConversationHandler.END

    return TUNGGU_ID_HAPUS

async def hapus_terima_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    teks = update.message.text.strip()
    if not teks.isdigit():
        await update.message.reply_text("❌ Masukkan nomor transaksi yang valid.")
        return TUNGGU_ID_HAPUS
    ctx.user_data["hapus_no"] = int(teks)
    kb = [["✅ Ya, hapus"], ["❌ Batal"]]
    await update.message.reply_text(
        f"Yakin hapus transaksi No.{teks}?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    return KONFIRMASI_HAPUS

async def hapus_konfirmasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if "Ya" not in update.message.text:
        await update.message.reply_text("❌ Dibatalkan.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    uid      = update.effective_user.id
    nama_tab = PENGGUNA[uid]
    no       = ctx.user_data.get("hapus_no")
    try:
        ok = hapus_baris_by_no(nama_tab, no)
        if ok:
            await update.message.reply_text(f"✅ Transaksi No.{no} berhasil dihapus!",
                                            reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text(f"❌ Transaksi No.{no} tidak ditemukan.",
                                            reply_markup=ReplyKeyboardRemove())
    except Exception:
        traceback.print_exc()
        await update.message.reply_text("❌ Gagal menghapus.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ── COMMAND: /reset ───────────────────────────────────────────────────────────
async def reset_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in PENGGUNA:
        await tolak(update); return ConversationHandler.END
    nama_tab = PENGGUNA[update.effective_user.id]
    kb = [["✅ Ya, reset semua"], ["❌ Batal"]]
    await update.message.reply_text(
        f"⚠️ *Reset Transaksi {nama_tab}*\n\n"
        f"Semua transaksi di tab kamu akan dihapus dari Google Sheets.\n\nYakin?",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return KONFIRMASI_RESET

async def reset_konfirmasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if "Ya" not in update.message.text:
        await update.message.reply_text("❌ Reset dibatalkan.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    uid      = update.effective_user.id
    nama_tab = PENGGUNA[uid]
    try:
        ws = get_sheet(nama_tab)
        ws.clear()
        header = ["No", "Tanggal", "Tipe", "Kategori", "Jumlah (Rp)", "Catatan"]
        ws.update([header], "A1:F1")
        ws.format("A1:F1", {
            "textFormat": {"bold": True,
                           "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "backgroundColor": {"red": 0.13, "green": 0.59, "blue": 0.33}
        })
        await update.message.reply_text(
            f"✅ Tab *{nama_tab}* berhasil direset!",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
    except Exception:
        traceback.print_exc()
        await update.message.reply_text("❌ Gagal reset.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ── COMMAND: /batal ───────────────────────────────────────────────────────────
async def batal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Dibatalkan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Starting BendaharaKu...")

    try:
        init_sheets()
    except Exception:
        traceback.print_exc()
        print("⚠️ Gagal inisialisasi sheet — cek GOOGLE_CREDENTIALS_B64 dan SHEET_URL")

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

    hapus_conv = ConversationHandler(
        entry_points=[CommandHandler("hapus", hapus_start)],
        states={
            TUNGGU_ID_HAPUS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, hapus_terima_id)],
            KONFIRMASI_HAPUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, hapus_konfirmasi)],
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

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("laporan", laporan))
    app.add_handler(catat_conv)
    app.add_handler(hapus_conv)
    app.add_handler(reset_conv)

    print("🤖 Bot berjalan...")
    app.run_polling()
