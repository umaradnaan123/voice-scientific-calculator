# app.py
import streamlit as st
import speech_recognition as sr
import math
import re
import time
import pandas as pd
import base64

# -------------------------
# Word maps
# -------------------------
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
    "plus": "+", "add": "+", "added": "+", "+": "+",
    "minus": "-", "subtract": "-", "less": "-", "-": "-",
    "into": "*", "times": "*", "multiply": "*", "x": "*", "*": "*",
    "divide": "/", "divided": "/", "over": "/", "by": "/", "/": "/",
}

FUNCTION_WORDS = {
    "sin": "sin(", "sine": "sin(",
    "cos": "cos(", "cosine": "cos(",
    "tan": "tan(", "tangent": "tan(",
    "log": "log(",    # base-10
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
IGNORES = {"of","the","and"}

# -------------------------
# number words -> numeric string
# supports decimals (point/dot)
# -------------------------
def number_words_to_str(tokens):
    if not tokens:
        return ""
    # if already numeric tokens (digits), join them
    if all(re.fullmatch(r"\d+(\.\d+)?", t) for t in tokens):
        return "".join(tokens)

    total = 0
    current = 0
    i = 0
    decimal_mode = False
    decimal_digits = []

    while i < len(tokens):
        w = tokens[i]
        if w in ("point","dot"):
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
        if w in SIMPLE:
            current += SIMPLE[w]
        elif w == "hundred":
            if current == 0:
                current = 1
            current *= 100
        elif w in ("thousand","million"):
            scale_val = SCALE[w]
            if current == 0:
                current = 1
            total += current * scale_val
            current = 0
        elif re.fullmatch(r"\d+(\.\d+)?", w):
            # numeric token
            current = current * 10 + int(float(w))
        else:
            break
        i += 1

    total += current
    if decimal_mode:
        dec = "".join(decimal_digits) if decimal_digits else "0"
        return f"{total}.{dec}"
    return str(total)

# -------------------------
# main parser: turns spoken text -> valid python expression string
# -------------------------
def parse_transcript_to_expr(text):
    text = text.lower()
    # normalize some multiword phrases
    text = text.replace("to the power of", " power ")
    text = text.replace("to the power", " power ")
    text = text.replace("power of", " power ")
    text = text.replace("raised to the power of", " power ")
    text = text.replace("square root of", " sqrt ")
    text = text.replace("reciprocal of", " reciprocal ")
    # remove filler words (we already normalized phrases above)
    text = re.sub(r"\b(of|the|and)\b", " ", text)

    # remove equal words
    for eq in EQUAL_WORDS:
        text = text.replace(eq, " ")

    raw = [t for t in re.split(r"\s+", text) if t]

    parts = []
    num_buf = []
    i = 0
    while i < len(raw):
        w = raw[i]

        # ignore filler tokens
        if w in IGNORES:
            i += 1
            continue

        # parentheses words
        if w in ("open","open_bracket","open-bracket") and i+1 < len(raw) and raw[i+1] in ("bracket","parenthesis"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append("("); i += 2; continue
        if w in ("close","close_bracket","close-bracket") and i+1 < len(raw) and raw[i+1] in ("bracket","parenthesis"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append(")"); i += 2; continue
        if w in ("open","open bracket","open parenthesis"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append("("); i += 1; continue
        if w in ("close","close bracket","close parenthesis"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append(")"); i += 1; continue

        # functions (sin, cos, tan, log, ln, sqrt)
        if w in FUNCTION_WORDS:
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            func = FUNCTION_WORDS[w]  # e.g. 'sin(' or 'sqrt('
            # attempt to immediately capture numeric argument
            j = i + 1
            arg_buf = []
            while j < len(raw) and (raw[j] in SIMPLE or raw[j] in SCALE or re.fullmatch(r"\d+(\.\d+)?", raw[j]) or raw[j] in ("point","dot","-")):
                arg_buf.append(raw[j]); j += 1
            if arg_buf:
                arg = number_words_to_str(arg_buf)
                parts.append(f"{func}{arg})")
                i = j
                continue
            else:
                # append function start, we'll let a following '(' or tokens fill it
                parts.append(func)
                i += 1
                continue

        # reciprocal handling
        if w == "reciprocal":
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            # capture next numeric group
            j = i + 1
            arg_buf = []
            while j < len(raw) and (raw[j] in SIMPLE or raw[j] in SCALE or re.fullmatch(r"\d+(\.\d+)?", raw[j]) or raw[j] in ("point","dot")):
                arg_buf.append(raw[j]); j += 1
            if arg_buf:
                arg = number_words_to_str(arg_buf)
                parts.append(f"(1/({arg}))")
                i = j
                continue
            else:
                i += 1
                continue

        # power keywords -> '**'
        if w in ("power","^","**","to"):
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append("**")
            i += 1
            continue

        # suffixes that apply to previous number
        if w in SUFFIXES:
            if num_buf:
                parts.append(number_words_to_str(num_buf)); num_buf=[]
            parts.append(SUFFIXES[w])
            i += 1
            continue

        # percent (treated as /100 suffix)
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

        # decimal inside number
        if w in ("point","dot"):
            num_buf.append(w); i += 1; continue

        # digits
        if re.fullmatch(r"\d+(\.\d+)?", w):
            num_buf.append(w); i += 1; continue

        # number words
        if w in SIMPLE or w in SCALE:
            num_buf.append(w); i += 1; continue

        # unknown token => flush numbers and skip
        if num_buf:
            parts.append(number_words_to_str(num_buf)); num_buf=[]
        i += 1

    # flush remaining number buffer
    if num_buf:
        parts.append(number_words_to_str(num_buf)); num_buf=[]

    # join parts into expression string
    expr = "".join(parts)  # no spaces needed: e.g. '10+2' or '10**2'
    # small cleanup: multiple operators collapse
    expr = re.sub(r"\s+", "", expr)
    expr = re.sub(r"\+\++", "+", expr)
    expr = re.sub(r"\-\-+", "-", expr)
    return expr

# -------------------------
# safe-ish evaluation
# -------------------------
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

    # convert 'n!' to factorial(n)
    expr2 = re.sub(r"(\d+(\.\d+)?|\([^\)]+\))\!", r"factorial(\1)", expr)
    # replace stray % with /100
    expr2 = expr2.replace("%", "/100")

    # sanitize allowed chars
    if not re.fullmatch(r"[0-9a-zA-Z_\+\-\*\/\.\(\),%!]+", expr2):
        raise ValueError("Invalid characters in expression")

    # evaluate with allowed functions only
    try:
        return eval(expr2, {"__builtins__": None}, allowed)
    except Exception as e:
        raise

# -------------------------
# utilities
# -------------------------
def df_to_download_link(df, name="history.csv"):
    csv = df.to_csv(index=False).encode()
    b64 = base64.b64encode(csv).decode()
    return f'<a href="data:file/csv;base64,{b64}" download="{name}">Download CSV</a>'

# -------------------------
# Streamlit UI
# -------------------------
st.set_page_config(page_title="Voice Scientific Calculator", layout="wide")
st.title("🎙️ Voice Scientific Calculator")
st.write("Speak natural math expressions and say 'equal' (or 'equals') to evaluate. Examples: 'ten plus two', '10 to the power of 3', 'square root of nine'.")

if "history" not in st.session_state:
    st.session_state.history = []

left, right = st.columns([2,1])

with right:
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

    st.subheader("History")
    if st.session_state.history:
        for it in reversed(st.session_state.history[-30:]):
            st.markdown(f"**{it['time']}**  \n• `{it['transcript']}` → `{it['expression']}` = **{it['result']}**")
    else:
        st.info("History empty")

with left:
    st.subheader("Transcript")
    transcript_box = st.empty()
    st.subheader("Expression")
    expr_box = st.empty()
    st.subheader("Result")
    result_box = st.empty()

    start = st.button("🎤 Start Listening")
    if start:
        if not use_mic:
            st.warning("Enable local microphone in Settings.")
        else:
            r = sr.Recognizer()
            full_text = ""
            status = st.empty()
            status.info("Listening... say 'equal' to finish")

            try:
                with sr.Microphone() as mic:
                    r.adjust_for_ambient_noise(mic, duration=0.6)
                    while True:
                        try:
                            audio = r.listen(mic, timeout=4, phrase_time_limit=6)
                        except sr.WaitTimeoutError:
                            transcript_box.write("...")
                            continue

                        try:
                            chunk = r.recognize_google(audio).lower()
                        except sr.UnknownValueError:
                            continue
                        except sr.RequestError as e:
                            status.error(f"Speech API error: {e}")
                            break

                        full_text += " " + chunk
                        transcript_box.write(full_text.strip())

                        if any(eq in chunk for eq in EQUAL_WORDS):
                            status.success("Equal detected — parsing...")
                            break

                expr = parse_transcript_to_expr(full_text)
                expr_box.code(expr)

                try:
                    val = evaluate_expression(expr)
                    result_box.success(val)
                    st.session_state.history.append({
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "transcript": full_text.strip(),
                        "expression": expr,
                        "result": str(val)
                    })
                except Exception as e:
                    result_box.error(f"Evaluation error: {e}")

            except Exception as e:
                st.error(f"Microphone error: {e}")
