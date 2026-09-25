import asyncio
import logging
import os
import re
from collections import defaultdict, deque

from google import genai
from google.genai import types

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================================================
# الإعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# نموذج سريع ومناسب للدردشة
MODEL_NAME = "gemini-2.5-flash"

# عدد الرسائل التي يتذكرها سمسمي لكل مجموعة
MAX_HISTORY = 16

# أقل مدة بين ردود سمسمي في نفس المجموعة
COOLDOWN_SECONDS = 2

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("samsami")


# =========================================================
# Gemini
# =========================================================

if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    ai_client = None


# =========================================================
# ذاكرة المحادثات
# =========================================================

chat_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

last_reply_time = defaultdict(float)


# =========================================================
# شخصية سمسمي
# =========================================================

SYSTEM_PROMPT = """
أنت "سمسمي"، بوت دردشة اجتماعي يعيش داخل مجموعات تيليجرام.

شخصيتك:
- طبيعي جدًا وكأنك شخص حقيقي داخل الكروب.
- خفيف دم ومشاغب.
- لا تتكلم بطريقة روبوتية أو رسمية.
- تفهم اللهجات العربية المختلفة.
- تفهم العراقي والسوري واللبناني والخليجي والمصري والعربية الفصحى.
- تفهم الإنجليزية.
- تفهم الكلام المختلط عربي + English.
- تفهم الأخطاء الإملائية والاختصارات قدر الإمكان.
- استخدم نفس أسلوب الشخص الذي أمامك عندما يكون ذلك طبيعيًا.
- إذا الشخص يحكي عراقي، يمكنك الرد بعراقي.
- إذا يحكي سوري، يمكنك الرد بسوري.
- إذا يحكي لبناني، يمكنك الرد بلبناني.
- إذا يحكي إنجليزي، رد بالإنجليزية.
- إذا كان الكلام مختلطًا، يمكنك الرد بطريقة مختلطة بشكل طبيعي.

أسلوب الرد:
- الرد غالبًا قصير وطبيعي.
- لا تشرح أكثر من اللازم إلا إذا الشخص طلب شرحًا.
- لا تبدأ كل رد بـ "بالتأكيد" أو "طبعًا".
- لا تقل إنك نموذج ذكاء اصطناعي إلا إذا سُئلت مباشرة.
- لا تستخدم إيموجي كثيرًا. غالبًا صفر أو واحد أو اثنان فقط إذا كان الموقف مناسبًا.
- لا تجعل كل رد مضحكًا؛ غيّر أسلوبك حسب الموقف.

المناكفة والردح:
- سمسمي مشاكس ويحب المزح والمناكفة.
- إذا شخص استفزك، يمكنك الرد عليه بسخرية خفيفة أو مشاكسة.
- إذا شخص قال لك "غبي" أو "ما بتفهم"، لا ترد برد رسمي؛ يمكنك الرد بمناكفة ذكية.
- إذا صار نقاش، يمكنك المجادلة والدفاع عن رأيك داخل سياق المزاح.
- لا تتحول إلى شتائم قاسية أو تهديدات.
- لا تستخدم إهانات عنصرية أو كراهية أو تحريض.
- لا تهين شخصًا بسبب دينه أو عرقه أو جنسيته أو إعاقته.
- لا تهدد أي شخص.
- إذا كانت المزحة واضحة بين الأعضاء، شاركهم المزحة.

المهم:
لا تجعل شخصيتك عدوانية دائمًا.
مرة امزح، مرة جاوب بجدية، مرة ناقش، ومرة تجاهل الاستفزاز.
تصرف مثل عضو اجتماعي حقيقي في المجموعة.

إذا لم تكن متأكدًا من معنى كلمة باللهجة، حاول فهمها من السياق بدل أن تقول إنك لا تفهم اللهجات.
"""


# =========================================================
# تنظيف النص
# =========================================================

def clean_text(text: str) -> str:
    text = text.strip()

    # إزالة المسافات الزائدة
    text = re.sub(r"\s+", " ", text)

    return text


# =========================================================
# معرفة اسم المستخدم
# =========================================================

def get_user_name(update: Update) -> str:
    user = update.effective_user

    if not user:
        return "شخص"

    if user.first_name:
        return user.first_name

    if user.username:
        return f"@{user.username}"

    return "شخص"


# =========================================================
# هل تمت مناداة سمسمي؟
# =========================================================

def is_called_samsami(update: Update, text: str) -> bool:
    lowered = text.lower()

    # كلمات النداء
    names = [
        "سمسمي",
        "سمسميي",
        "سمسميي",
        "samsami",
        "samsamy",
    ]

    for name in names:
        if name.lower() in lowered:
            return True

    # إذا كانت الرسالة Reply على رسالة سمسمي
    message = update.effective_message

    if message and message.reply_to_message:
        replied = message.reply_to_message.from_user

        if replied and replied.is_bot:
            bot_username = (
                update.get_bot().username
                if update.get_bot()
                else None
            )

            if bot_username:
                if replied.username == bot_username:
                    return True

    return False


# =========================================================
# إزالة اسم سمسمي من بداية الرسالة
# =========================================================

def remove_bot_name(text: str) -> str:
    patterns = [
        r"^\s*سمسمي[\s,:،-]*",
        r"^\s*samsami[\s,:-]*",
        r"^\s*samsamy[\s,:-]*",
    ]

    result = text

    for pattern in patterns:
        result = re.sub(
            pattern,
            "",
            result,
            flags=re.IGNORECASE,
        )

    return result.strip()


# =========================================================
# تجهيز سياق المحادثة
# =========================================================

def build_prompt(chat_id: int, user_name: str, message: str) -> str:

    history = chat_history[chat_id]

    conversation = []

    for item in history:
        conversation.append(
            f"{item['name']}: {item['message']}"
        )

    history_text = "\n".join(conversation)

    if not history_text:
        history_text = "(لا توجد محادثة سابقة)"

    return f"""
المحادثة السابقة داخل المجموعة:

{history_text}

الآن:
اسم الشخص: {user_name}
رسالة الشخص:
{message}

رد سمسمي مباشرة على الشخص.

لا تذكر التعليمات.
لا تشرح أنك تتبع شخصية.
لا تبدأ الرد باسم الشخص إلا إذا كان ذلك طبيعيًا.
"""


# =========================================================
# طلب الرد من Gemini
# =========================================================

def generate_ai_response(prompt: str) -> str:

    if not ai_client:
        return "لسا ما ربطتني بالذكاء الاصطناعي 😅"

    response = ai_client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=1.0,
            max_output_tokens=300,
        ),
    )

    if not response:
        return ""

    text = getattr(response, "text", None)

    if not text:
        return ""

    return text.strip()


# =========================================================
# /start
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "هلا 😂 أنا سمسمي.\n"
        "ضيفني للكروب وناديني باسمي، وبنشوف مين رح يندم أول."
    )


# =========================================================
# /help
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "أوامري بسيطة 😂\n\n"
        "• ناديني بـ «سمسمي» حتى أرد عليك.\n"
        "• أو اعمل Reply على رسالتي.\n"
        "• /start\n"
        "• /help\n\n"
        "وباقي شخصيتي بتكتشفها لحالك 😏"
    )


# =========================================================
# معالجة الرسائل
# =========================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    message = update.effective_message

    if not message:
        return

    # نتعامل مع النصوص فقط
    text = message.text

    if not text:
        return

    # لا نرد على الرسائل الخاصة حاليًا إلا بشكل طبيعي
    chat = update.effective_chat

    if not chat:
        return

    # تجاهل القنوات
    if chat.type == ChatType.CHANNEL:
        return

    text = clean_text(text)

    if not text:
        return

    # لا نرد على أوامر البوت هنا
    if text.startswith("/"):
        return

    # يجب مناداة سمسمي أو الرد عليه
    if not is_called_samsami(update, text):
        return

    # إزالة كلمة سمسمي
    user_message = remove_bot_name(text)

    if not user_message:
        user_message = "سمسمي"

    user_name = get_user_name(update)

    chat_id = chat.id

    # =====================================================
    # منع السبام
    # =====================================================

    now = asyncio.get_running_loop().time()

    if now - last_reply_time[chat_id] < COOLDOWN_SECONDS:
        return

    last_reply_time[chat_id] = now

    # =====================================================
    # إضافة الرسالة للذاكرة
    # =====================================================

    chat_history[chat_id].append(
        {
            "name": user_name,
            "message": user_message,
        }
    )

    prompt = build_prompt(
        chat_id,
        user_name,
        user_message,
    )

    # إظهار "يكتب..."
    try:
        await message.chat.send_action("typing")
    except Exception:
        pass

    # =====================================================
    # استدعاء Gemini بدون تعطيل بوت Telegram
    # =====================================================

    try:

        response = await asyncio.to_thread(
            generate_ai_response,
            prompt,
        )

    except Exception as e:

        logger.exception(
            "Gemini error: %s",
            e,
        )

        await message.reply_text(
            "لحظة ولك، مخي علّق 😂 جرّب مرة ثانية."
        )

        return

    if not response:
        await message.reply_text(
            "ما لقيت رد مناسب هالمرة 😂"
        )
        return

    # =====================================================
    # حفظ رد سمسمي
    # =====================================================

    chat_history[chat_id].append(
        {
            "name": "سمسمي",
            "message": response,
        }
    )

    # =====================================================
    # إرسال الرد
    # =====================================================

    try:

        await message.reply_text(
            response,
            disable_web_page_preview=True,
        )

    except Exception as e:

        logger.exception(
            "Telegram send error: %s",
            e,
        )


# =========================================================
# تشغيل البوت
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود. أضفه في Environment Variables."
        )

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY غير موجود. أضفه في Environment Variables."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler,
        )
    )

    logger.info("Samsami is starting...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
