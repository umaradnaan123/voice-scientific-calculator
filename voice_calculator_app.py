# app.py
import streamlit as st
import speech_recognition as sr
import math
import re
import time
import pandas as pd
import base64

# ----------------------------
# WORD MAPS
# ----------------------------
SIMPLE = {
    "zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,
    "six":6,"seven":7,"eight":8,"nine":9,"ten":10,
    "eleven":11,"twelve":12,"thirteen":13,"fourteen":14,
    "fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,
    "nineteen":19,"twenty":20,"thirty":30,"forty":40,"fifty":50,
    "sixty":60,"seventy":70,"eighty":80,"ninety":90
}
SCALE = {"hundred":100, "thousand":1000, "million":1000000}

OPERATORS = {
    "plus": "+", "add": "+", "added": "+", "+":"+",
    "minus": "-", "subtract": "-", "less": "-", "-":"-",
    "into": "*", "times": "*", "multiply": "*", "x": "*", "*":"*",
    "divide": "/", "divided": "/", "over": "/", "by": "/", "/":"/",
}

FUNCTION_WORDS = {
    "sin": "sin(", "sine": "sin(",
    "cos": "cos(", "cosine": "cos(",
    "tan": "tan(", "tangent": "tan(",
    "log": "log(",    # log -> base10
    "ln": "ln(",
    "sqrt": "sqrt(", "square root": "sqrt("
}

SUFFIXES = {
    "square": "**2", "squared": "**2",
    "cube": "**3", "cubed": "**3",
    "factorial": "!"
}

RECIPROCAL_WORDS = {"reciprocal", "reciprocal of", "one over", "one by"}
EQUAL_WORDS = {"equal", "equals", "equal to", "=", "is"}
# words to ignore or normalize
IGNORES = {"of", "the", "and"}

# ----------------------------
# Helpers: number words -> numeric string
# ----------------------------
def number_words_to_str(tokens):
    """Convert list of number-word tokens to numeric string, handle 'point' for decimals."""
    if not tokens:
        return ""

    # if tokens are numeric strings already, join carefully
    if all(re.fullmatch(r"\d+(\.\d+)?", t) for t in tokens):
        # join tokens but keep separation for multi-digit tokens (they are numbers already)
        return "".join(tokens)

    total = 0
    current = 0
    i = 0
    decimal_mode = False
    decimal_digits = []

    while i < len(tokens):
        t = tokens[i]
        if t in ("point","dot"):
            decimal_mode = True
            i += 1
            while i < len(tokens):
                d = tokens[i]
                if d in SIMPLE:
                    decimal_digits.append(str(SIMPLE[d]))
                elif re.fullmatch(r"\d", d):
                    decimal_digits.append(d)
                else:
                    break
                i += 1
            break
        if t in SIMPLE:
            current += SIMPLE[t]
        elif t == "hundred":
            if current == 0:
                current = 1
            current *= 100
        elif t in ("thousand","million"):
            scale_val = SCALE[t]
            if current == 0:
                current = 1
            total += current * scale_val
            current = 0
        elif re.fullmatch(r"\d+(\.\d+)?", t):
            # numeric token encountered, append
            current = current * 10 + int(float(t))
        else:
            # unknown token — stop parsing
            break
        i += 1

    total += current
    if decimal_mode:
        dec = "".join(decimal_digits) if decimal_digits else "0"
        return f"{total}.{dec}"
    return str(total)

# ----------------------------
# Robust parser that handles "to the power of", "power", suffixes, functions, etc.
# ----------------------------
def parse_transcript_to_expr(text):
    text = text.lower()
    # normalize some phrases
    text = text.replace("to the power of", " power ")
    text = text.replace("to the power", " power ")
    text = text.replace("power of", " power ")
    text = text.replace("raised to the power of", " power ")
    text = text.replace("square root of", "sqrt ")
    text = text.replace("reciprocal of", "reciprocal ")
    # remove filler words 'of', 'the' but keep them in contexts where necessary handled earlier
    # we'll remove 'of' after we handled phrases
    text = re.sub(r"\b(of|the|and)\b", " ", text)

    # remove equal words
    for eq in EQUAL_WORDS:
        text = text.replace(eq, " ")

    # split into tokens
    raw = [t for t in re.split(r"\s+", text) if t]

    parts = []            # final expression pieces (strings)
    num_buf = []          # buffer for number words/digits
    i = 0
    while i < len(raw):
        w = raw[i]

        # skip ignorable words
        if w in IGNORES:
            i += 1
            continue

        # handle parentheses words explicitly
        if w in ("open","openbracket","open-bracket") and i+1 < len(raw) and raw[i+1] in ("bracket","parenthesis"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append("("); i += 2; continue
        if w in ("close","closebracket","close-bracket") and i+1 < len(raw) and raw[i+1] in ("bracket","parenthesis"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append(")"); i += 2; continue
        if w in ("open","open_bracket","open_parenthesis","open bracket","open parenthesis"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append("("); i += 1; continue
        if w in ("close","close_bracket","close_parenthesis","close bracket","close parenthesis"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append(")"); i += 1; continue

        # functions like sin, cos, tan, log, ln, sqrt
        if w in FUNCTION_WORDS:
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            func = FUNCTION_WORDS[w]  # e.g., 'sin(' or 'sqrt('
            # try to consume next token(s) as single numeric argument
            j = i + 1
            arg_buf = []
            # allow number words/digits/point
            while j < len(raw) and (raw[j] in SIMPLE or raw[j] in SCALE or re.fullmatch(r"\d+(\.\d+)?", raw[j]) or raw[j] in ("point","dot","-")):
                arg_buf.append(raw[j]); j += 1
            if arg_buf:
                arg_str = number_words_to_str(arg_buf)
                parts.append(f"{func}{arg_str})")
                i = j
                continue
            else:
                # no immediate numeric argument; append func and let next tokens form the argument (e.g., sin ( 30 ) )
                parts.append(func)
                i += 1
                continue

        # 'reciprocal' — produce 1/(next)
        if w == "reciprocal":
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            # try to capture next number/expression
            j = i + 1
            if j < len(raw) and raw[j] == "(":
                # find match parentheses block
                depth = 0; k = j; sub = []
                while k < len(raw):
                    sub.append(raw[k])
                    if raw[k] == "(":
                        depth += 1
                    if raw[k] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                inner = " ".join(sub)
                inner_expr = parse_transcript_to_expr(inner)
                parts.append(f"(1/({inner_expr}))")
                i = k + 1
                continue
            else:
                # capture next numeric tokens
                arg_buf = []
                j = i + 1
                while j < len(raw) and (raw[j] in SIMPLE or raw[j] in SCALE or re.fullmatch(r"\d+(\.\d+)?", raw[j]) or raw[j] in ("point","dot")):
                    arg_buf.append(raw[j]); j += 1
                if arg_buf:
                    arg_str = number_words_to_str(arg_buf)
                    parts.append(f"(1/({arg_str}))")
                    i = j
                    continue
                else:
                    # nothing next — ignore
                    i += 1
                    continue

        # power operator keywords → '**'
        if w in ("power","^","**","to"):
            # specifically handle phrases like "10 to the power of 2"
            # flush number buffer
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            # append exponent operator
            parts.append("**")
            i += 1
            continue

        # suffixes (square/cubed/factorial)
        if w in SUFFIXES:
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append(SUFFIXES[w])
            i += 1
            continue

        # percent as suffix (e.g., fifty percent)
        if w in ("percent","percentage","%"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append("/100")
            i += 1
            continue

        # operators
        if w in OPERATORS:
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append(OPERATORS[w])
            i += 1
            continue

        # decimal point word/dot inside a number
        if w in ("point","dot"):
            num_buf.append(w)
            i += 1
            continue

        # numeric tokens
        if re.fullmatch(r"\d+(\.\d+)?", w):
            num_buf.append(w)
            i += 1
            continue

        # number words
        if w in SIMPLE or w in SCALE:
            num_buf.append(w)
            i += 1
            continue

        # fallback: if unknown token that contains digits (like "10x") try to separate digits
        m = re.match(r"^(\d+)([a-zA-Z]+)$", w)
        if m:
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append(m.group(1))
            # leave the text part for next loop iteration by replacing token in raw
            raw[i] = m.group(2)
            continue

        # unknown token: flush number buffer and skip
        if num_buf:
            parts.append(number_words_to_str(num_buf)); num_buf=[]
        i += 1

    # flush remainder
    if num_buf:
        parts.append(number_words_to_str(num_buf)); num_buf=[]

    # post-process parts: remove accidental adjacent operators or spaces
    # join with space to avoid number-concatenation bugs (e.g., "10 2" becomes "10 2" but we want "10**2" when '**' present)
    # however '**' must be separated as operator
    expr = " ".join(parts)

    # tidy fixes:
    # - collapse multiple spaces
    expr = re.sub(r"\s+", " ", expr).strip()
    # - replace ' ** ' with '**' (so 10 ** 2 is valid python)
    expr = expr.replace(" ** ", " ** ")
    # Remove stray leading/trailing operators
    expr = expr.strip()
    return expr

# ----------------------------
# Safe-ish evaluator (uses allowed math functions)
# ----------------------------
def evaluate_expression(expr):
    allowed = {
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": lambda x: math.log10(x),
        "ln": lambda x: math.log(x),
        "sqrt": math.sqrt,
        "factorial": math.factorial
    }

    if not expr or expr.strip() == "":
        raise ValueError("Empty expression")

    # convert n! to factorial(n)
    expr2 = re.sub(r"(\d+(\.\d+)?|\([^\)]+\))\!", r"factorial(\1)", expr)
    # replace stray '%' if any with /100
    expr2 = expr2.replace("%", "/100")

    # simple sanitization: allow digits, letters (for function names), operators and parentheses, dot, spaces
    if not re.fullmatch(r"[0-9a-zA-Z_\+\-\*\/\.\(\) ,%!]+", expr2):
        raise ValueError("Expression contains invalid characters")

    # finally evaluate with restricted globals
    try:
        value = eval(expr2, {"__builtins__": None}, allowed)
    except Exception as e:
        raise

    return value

# ----------------------------
# Utilities
# ----------------------------
def df_to_download_link(df, name="history.csv"):
    csv = df.to_csv(index=False).encode()
    b64 = base64.b64encode(csv).decode()
    return f'<a href="data:file/csv;base64,{b64}" download="{name}">Download</a>'

# ----------------------------
# Streamlit UI (attractive)
# ----------------------------
st.set_page_config(page_title="Voice Scientific Calculator", layout="wide")
st.markdown("""
<style>
body { background: linear-gradient(135deg,#0f1724 0%,#071026 100%); color: #e6eef8; }
.card { background: rgba(255,255,255,0.03); border-radius: 12px; padding: 16px; margin-bottom: 12px; }
.big { font-weight:700; font-size:18px; }
.btn { border-radius:10px; padding:10px 14px; }
.history { max-height:420px; overflow:auto; padding:10px; border-left:3px solid rgba(124,58,237,0.9); background: rgba(255,255,255,0.02); border-radius:8px; }
.mic { color:#ff7b7b; font-weight:700; }
</style>
""", unsafe_allow_html=True)

st.markdown("<h1>🎙️ Voice Scientific Calculator</h1>", unsafe_allow_html=True)
st.write("Say math naturally (e.g. 'ten to the power of two equal to', 'five squared equal').")

if "history" not in st.session_state:
    st.session_state.history = []

left, right = st.columns([2,1])

with right:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Settings")
    use_mic = st.checkbox("Use Microphone (local)", value=True)
    if st.button("Clear History"):
        st.session_state.history = []
        st.success("History cleared")
    if st.button("Download History CSV"):
        if st.session_state.history:
            df = pd.DataFrame(st.session_state.history)
            st.markdown(df_to_download_link(df), unsafe_allow_html=True)
        else:
            st.info("No history yet")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="card history">', unsafe_allow_html=True)
    st.subheader("History")
    if st.session_state.history:
        for it in reversed(st.session_state.history[-40:]):
            st.markdown(f"**{it['time']}**  \n🔊 `{it['transcript']}`  \n➡ `{it['expression']}` = **{it['result']}**")
    else:
        st.info("No calculations yet. Click Start and speak.")
    st.markdown('</div>', unsafe_allow_html=True)

with left:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Transcript")
    transcript_box = st.empty()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Expression")
    expr_box = st.empty()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Result")
    result_box = st.empty()
    st.markdown('</div>', unsafe_allow_html=True)

    start = st.button("🎤 Start Listening", key="start")
    stop = st.button("⛔ Stop", key="stop")

    if start:
        if not use_mic:
            st.warning("Enable microphone in Settings.")
        else:
            recognizer = sr.Recognizer()
            full_text = ""
            status = st.empty()
            status.info("🎧 Listening... say 'equal' to finish")

            try:
                with sr.Microphone() as mic:
                    recognizer.adjust_for_ambient_noise(mic, duration=0.6)
                    while True:
                        try:
                            audio = recognizer.listen(mic, timeout=4, phrase_time_limit=6)
                        except sr.WaitTimeoutError:
                            # still listening
                            transcript_box.write("...")
                            continue

                        try:
                            chunk = recognizer.recognize_google(audio).lower()
                        except sr.UnknownValueError:
                            continue
                        except sr.RequestError as e:
                            status.error(f"Speech API error: {e}")
                            break

                        full_text += " " + chunk
                        transcript_box.markdown(f"<span class='mic'>{full_text.strip()}</span>", unsafe_allow_html=True)

                        if any(eq in chunk for eq in EQUAL_WORDS):
                            status.success("🛑 'Equal' detected — parsing...")
                            break

                expr = parse_transcript_to_expr(full_text)
                expr_box.code(expr)

                try:
                    value = evaluate_expression(expr)
                    result_box.success(value)

                    st.session_state.history.append({
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "transcript": full_text.strip(),
                        "expression": expr,
                        "result": str(value)
                    })
                except Exception as e:
                    result_box.error(f"Evaluation error: {e}")

            except Exception as e:
                st.error(f"Microphone error: {e}")
