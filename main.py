"""
main.py – Märchenbuch Telegram Bot (MVP)
=========================================
Flow:
  1. /start oder Foto → Zeichnung hochladen
  2. Name → Alter → Sprache → Stimmung → Storywunsch → Widmung
  3. 3 Referenzbilder generieren, als Auswahl schicken
  4. User wählt per Inline-Button
  5. Buch generieren, PDF senden

Replit-Secrets:
  TELEGRAM_TOKEN
  OPENAI_API_KEY
"""

import os
import uuid
import asyncio
import logging
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import pipeline

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("maerchenbuch")

# ── Secrets ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WORK_BASE = Path("orders")
WORK_BASE.mkdir(exist_ok=True)

# ── Conversation States ──────────────────────────────────────────────────────
(
    PHOTO,
    NAME,
    AGE,
    LANGUAGE,
    MOOD,
    STORY_WISH,
    DEDICATION,
    CONSENT,
    GENERATING_REFS,
    CHOOSE_REF,
    GENERATING_BOOK,
) = range(11)


# ══════════════════════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN
# ══════════════════════════════════════════════════════════════════════════════

def new_order(user_id: int) -> dict:
    """Erstellt ein leeres Order-Dict."""
    oid = f"{user_id}_{uuid.uuid4().hex[:8]}"
    work = str(WORK_BASE / oid)
    Path(work).mkdir(parents=True, exist_ok=True)
    return {
        "order_id": oid,
        "user_id": user_id,
        "child_name": "",
        "child_age": 5,
        "language": "de",
        "mood": "abenteuer",
        "story_wish": "",
        "dedication": "",
        "drawing_path": "",
        "work_dir": work,
    }


# ══════════════════════════════════════════════════════════════════════════════
# BOT-HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Begrüßung und Aufforderung zum Zeichnungs-Upload."""
    ctx.user_data["order"] = new_order(update.effective_user.id)

    await update.message.reply_text(
        "✨ *Willkommen beim Märchenbuch-Bot!*\n\n"
        "Schick mir ein Foto einer Kinderzeichnung – "
        "ich mache daraus ein echtes, personalisiertes Märchenbuch.\n\n"
        "📸 *Schick mir jetzt die Zeichnung als Foto.*",
        parse_mode="Markdown",
    )
    return PHOTO


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Empfängt die Zeichnung und fragt nach dem Kindesnamen."""
    order = ctx.user_data.get("order")
    if not order:
        order = new_order(update.effective_user.id)
        ctx.user_data["order"] = order

    # Größtes verfügbares Foto laden
    photo = update.message.photo[-1]
    file = await photo.get_file()
    save_path = os.path.join(order["work_dir"], "zeichnung.jpg")
    await file.download_to_drive(save_path)
    order["drawing_path"] = save_path

    logger.info(f"Zeichnung empfangen: {save_path} ({photo.width}x{photo.height})")

    await update.message.reply_text(
        "🎨 Schöne Zeichnung! Jetzt brauche ich ein paar Angaben.\n\n"
        "👦 *Wie heißt das Kind?*",
        parse_mode="Markdown",
    )
    return NAME


async def handle_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Speichert den Namen, fragt nach dem Alter."""
    name = update.message.text.strip()
    if not name or len(name) > 50:
        await update.message.reply_text("Bitte gib einen gültigen Namen ein (max. 50 Zeichen).")
        return NAME

    ctx.user_data["order"]["child_name"] = name

    await update.message.reply_text(
        f"✓ *{name}* – schöner Name!\n\n"
        f"🎂 *Wie alt ist {name}?* (Zahl eingeben)",
        parse_mode="Markdown",
    )
    return AGE


async def handle_age(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Speichert das Alter, fragt nach der Sprache."""
    try:
        age = int(update.message.text.strip())
        if not 2 <= age <= 12:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Bitte gib ein Alter zwischen 2 und 12 ein.")
        return AGE

    ctx.user_data["order"]["child_age"] = age

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇩🇪 Deutsch", callback_data="lang_de"),
            InlineKeyboardButton("🇬🇧 Englisch", callback_data="lang_en"),
        ]
    ])
    await update.message.reply_text(
        "🌍 *In welcher Sprache soll die Geschichte sein?*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return LANGUAGE


async def handle_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Speichert die Sprache, fragt nach der Stimmung."""
    query = update.callback_query
    await query.answer()

    lang = query.data.replace("lang_", "")
    ctx.user_data["order"]["language"] = lang
    lang_label = "Deutsch" if lang == "de" else "Englisch"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗺️ Abenteuer", callback_data="mood_abenteuer")],
        [InlineKeyboardButton("✨ Magie", callback_data="mood_magie")],
        [InlineKeyboardButton("👨‍👩‍👧 Familien-Reise", callback_data="mood_familie")],
        [InlineKeyboardButton("🤝 Freundschaft", callback_data="mood_freundschaft")],
    ])
    await query.edit_message_text(
        f"✓ Sprache: *{lang_label}*\n\n"
        "🎭 *Welche Stimmung soll die Geschichte haben?*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return MOOD


async def handle_mood(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Speichert die Stimmung, fragt nach optionalem Storywunsch."""
    query = update.callback_query
    await query.answer()

    mood = query.data.replace("mood_", "")
    ctx.user_data["order"]["mood"] = mood
    mood_labels = {
        "abenteuer": "🗺️ Abenteuer",
        "magie": "✨ Magie",
        "familie": "👨‍👩‍👧 Familien-Reise",
        "freundschaft": "🤝 Freundschaft",
    }

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏩ Überspringen", callback_data="skip_story")],
    ])
    await query.edit_message_text(
        f"✓ Stimmung: *{mood_labels.get(mood, mood)}*\n\n"
        "📝 *Hast du einen Storywunsch?*\n"
        "z.B. _'Das Kind findet einen magischen Weg in einen Zauberwald...'_\n\n"
        "Schreib deinen Wunsch oder drück Überspringen.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    return STORY_WISH


async def handle_story_wish_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Speichert den Storywunsch, fragt nach Widmung."""
    ctx.user_data["order"]["story_wish"] = update.message.text.strip()
    return await _ask_dedication(update.message, ctx)


async def handle_story_wish_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Überspringt den Storywunsch."""
    query = update.callback_query
    await query.answer()
    ctx.user_data["order"]["story_wish"] = ""
    return await _ask_dedication(query.message, ctx, edit=True)


async def _ask_dedication(message, ctx, edit=False):
    """Fragt nach der Widmung."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏩ Überspringen", callback_data="skip_dedication")],
    ])
    text = (
        "💌 *Möchtest du eine Widmung hinzufügen?*\n"
        "z.B. _„Für Mia, zum 5. Geburtstag ♥"_\n\n"
        "Schreib deine Widmung oder drück Überspringen."
    )
    if edit:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return DEDICATION


async def handle_dedication_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Speichert die Widmung und zeigt Zusammenfassung + Datenschutz-Einwilligung."""
    ctx.user_data["order"]["dedication"] = update.message.text.strip()
    return await _show_consent(update.message, ctx)


async def handle_dedication_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Überspringt die Widmung."""
    query = update.callback_query
    await query.answer()
    ctx.user_data["order"]["dedication"] = ""
    return await _show_consent(query.message, ctx, edit=True)


async def _show_consent(message, ctx, edit=False):
    """Zeigt Zusammenfassung und Datenschutz-Einwilligung."""
    order = ctx.user_data["order"]
    lang_label = "Deutsch" if order["language"] == "de" else "Englisch"
    mood_labels = {
        "abenteuer": "Abenteuer", "magie": "Magie",
        "familie": "Familien-Reise", "freundschaft": "Freundschaft",
    }

    summary = (
        "📋 *Zusammenfassung:*\n"
        f"• Kind: *{order['child_name']}*, {order['child_age']} Jahre\n"
        f"• Sprache: {lang_label}\n"
        f"• Stimmung: {mood_labels.get(order['mood'], order['mood'])}\n"
        f"• Storywunsch: {order['story_wish'] or '–'}\n"
        f"• Widmung: {order['dedication'] or '–'}\n\n"
        "🔒 *Datenschutz:* Deine Zeichnung und Angaben werden "
        "ausschließlich zur Bucherstellung verwendet und nach 30 Tagen "
        "automatisch gelöscht.\n\n"
        "Alles korrekt?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Los geht's!", callback_data="consent_yes"),
            InlineKeyboardButton("❌ Abbrechen", callback_data="consent_no"),
        ]
    ])

    if edit:
        await message.edit_text(summary, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await message.reply_text(summary, reply_markup=keyboard, parse_mode="Markdown")
    return CONSENT


async def handle_consent(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Verarbeitet die Einwilligung, startet Referenzbild-Generierung."""
    query = update.callback_query
    await query.answer()

    if query.data == "consent_no":
        await query.edit_message_text(
            "❌ Abgebrochen. Schick /start um neu zu beginnen."
        )
        return ConversationHandler.END

    order = ctx.user_data["order"]
    await query.edit_message_text(
        "🎨 *Referenzbilder werden generiert…*\n\n"
        "Ich analysiere die Zeichnung und erstelle 3 Illustrationsvarianten. "
        "Das dauert ca. 30–60 Sekunden.",
        parse_mode="Markdown",
    )

    # Referenzbilder in Background generieren
    try:
        chars = pipeline.analyze_drawing(order)
        ctx.user_data["chars"] = chars

        ref_paths = pipeline.generate_reference_images(order, chars)
        ctx.user_data["ref_paths"] = ref_paths

        # Referenzbilder senden
        for i, path in enumerate(ref_paths):
            if path and os.path.exists(path):
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        f"✅ Variante {i+1} wählen",
                        callback_data=f"ref_{i}",
                    )]
                ])
                with open(path, "rb") as f:
                    await query.message.reply_photo(
                        photo=InputFile(f),
                        caption=f"🎨 Variante {i+1}",
                        reply_markup=keyboard,
                    )

        await query.message.reply_text(
            "👆 *Wähle die Variante, die dir am besten gefällt.*\n"
            "Diese Figuren werden für das gesamte Buch verwendet.",
            parse_mode="Markdown",
        )
        return CHOOSE_REF

    except Exception as e:
        logger.error(f"Referenzbild-Fehler: {e}", exc_info=True)
        await query.message.reply_text(
            "⚠️ Beim Generieren ist ein Fehler aufgetreten. "
            "Bitte versuch es nochmal mit /start."
        )
        return ConversationHandler.END


async def handle_ref_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """User hat ein Referenzbild gewählt. Startet Buchgenerierung."""
    query = update.callback_query
    await query.answer()

    choice = int(query.data.replace("ref_", ""))
    ctx.user_data["ref_choice"] = choice
    order = ctx.user_data["order"]

    status_msg = await query.message.reply_text(
        f"✅ Variante {choice + 1} gewählt!\n\n"
        "📖 *Dein Buch wird jetzt erstellt…*\n"
        "Story schreiben → 11 Illustrationen → PDF bauen\n"
        "Das dauert ca. 3–5 Minuten. Ich melde mich! ☕",
        parse_mode="Markdown",
    )

    # Pipeline in Thread ausführen damit der Bot nicht blockiert
    try:
        chars = ctx.user_data.get("chars", {})

        async def run_pipeline():
            loop = asyncio.get_event_loop()
            pdf_path = await loop.run_in_executor(
                None,
                pipeline.run_full_pipeline,
                order,
                choice,
                None,  # progress_cb (könnte man für Status-Updates nutzen)
            )
            return pdf_path

        pdf_path = await run_pipeline()

        if pdf_path and os.path.exists(pdf_path):
            child = order["child_name"]
            with open(pdf_path, "rb") as f:
                await query.message.reply_document(
                    document=InputFile(f, filename=f"{child}s_Maerchenbuch.pdf"),
                    caption=(
                        f"📚 *{child}s Märchenbuch ist fertig!* 🎉\n\n"
                        f"13 Seiten · A4 druckfertig · mit Schnittmarken\n\n"
                        f"Viel Freude damit! Schick /start für ein neues Buch."
                    ),
                    parse_mode="Markdown",
                )
        else:
            await query.message.reply_text(
                "⚠️ PDF konnte nicht erstellt werden. Bitte versuch es nochmal."
            )

    except Exception as e:
        logger.error(f"Pipeline-Fehler: {e}", exc_info=True)
        await query.message.reply_text(
            "⚠️ Beim Erstellen ist ein Fehler aufgetreten. "
            "Bitte versuch es nochmal mit /start."
        )

    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Bricht die Konversation ab."""
    await update.message.reply_text(
        "❌ Abgebrochen. Schick /start um ein neues Buch zu starten."
    )
    return ConversationHandler.END


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Hilfe-Befehl."""
    await update.message.reply_text(
        "📚 *Märchenbuch-Bot – Hilfe*\n\n"
        "So funktioniert's:\n"
        "1. Schick /start\n"
        "2. Lade ein Foto einer Kinderzeichnung hoch\n"
        "3. Beantworte ein paar Fragen\n"
        "4. Wähle einen Illustrationsstil\n"
        "5. Erhalte dein fertiges Märchenbuch als PDF!\n\n"
        "/start – Neues Buch starten\n"
        "/cancel – Aktuellen Vorgang abbrechen",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# BOT STARTEN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """Bot aufsetzen und starten."""
    if not TELEGRAM_TOKEN:
        print("⚠️  TELEGRAM_TOKEN nicht gesetzt!")
        print("   Setze den Token als Replit Secret.")
        return

    if not os.environ.get("OPENAI_API_KEY"):
        print("⚠️  OPENAI_API_KEY nicht gesetzt!")
        print("   Setze den Key als Replit Secret.")
        return

    # Bot aufbauen
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Conversation Handler: der 5-Schritt-Flow
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.PHOTO, handle_photo),
        ],
        states={
            PHOTO: [
                MessageHandler(filters.PHOTO, handle_photo),
            ],
            NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name),
            ],
            AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_age),
            ],
            LANGUAGE: [
                CallbackQueryHandler(handle_language, pattern=r"^lang_"),
            ],
            MOOD: [
                CallbackQueryHandler(handle_mood, pattern=r"^mood_"),
            ],
            STORY_WISH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_story_wish_text),
                CallbackQueryHandler(handle_story_wish_skip, pattern=r"^skip_story$"),
            ],
            DEDICATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dedication_text),
                CallbackQueryHandler(handle_dedication_skip, pattern=r"^skip_dedication$"),
            ],
            CONSENT: [
                CallbackQueryHandler(handle_consent, pattern=r"^consent_"),
            ],
            CHOOSE_REF: [
                CallbackQueryHandler(handle_ref_choice, pattern=r"^ref_\d$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start", cmd_start),
        ],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", cmd_help))

    logger.info("🚀 Märchenbuch-Bot gestartet!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
