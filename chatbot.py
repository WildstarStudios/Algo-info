import json
import os
import re
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
import difflib

KNOWLEDGE_FILE = "knowledge.json"

def load_knowledge():
    if os.path.exists(KNOWLEDGE_FILE):
        with open(KNOWLEDGE_FILE, "r") as f:
            return json.load(f)
    else:
        return {"knowledge": []}

def save_knowledge(kb):
    with open(KNOWLEDGE_FILE, "w") as f:
        json.dump(kb, f, indent=2)

def tokenize(text):
    """Split text into lowercase words without punctuation."""
    return re.findall(r"\b\w+\b", text.lower())

def remove_articles(text):
    return re.sub(r"\b(the|a|an)\b", "", text, flags=re.IGNORECASE).strip()

def try_math(input_text):
    """Detect and safely evaluate simple math expressions."""
    replacements = {
        "plus": "+",
        "minus": "-",
        "times": "*",
        "x": "*",
        "divided by": "/"
    }
    text = input_text.lower()
    for word, symbol in replacements.items():
        text = text.replace(word, symbol)

    if re.fullmatch(r"[0-9+\-*/ ().]+", text):
        try:
            return str(eval(text, {"__builtins__": {}}))
        except:
            return None
    return None

def find_association(kb, subject=None, predicate=None, obj=None):
    results = []
    for entry in kb["knowledge"]:
        if entry["type"] != "association":
            continue
        assoc = entry["content"]
        if subject and assoc.get("subject") != subject:
            continue
        if predicate and assoc.get("predicate") != predicate:
            continue
        if obj and assoc.get("object") != obj:
            continue
        results.append(assoc)
    return results

def find_facts_or_concepts(kb, input_text):
    input_tokens = set(tokenize(input_text))
    results = []
    for entry in kb["knowledge"]:
        if entry["type"] in ["fact", "concept"]:
            content_tokens = set(tokenize(entry["content"]))
            if input_tokens & content_tokens:
                results.append(entry["content"])
    return results

def parse_question(text):
    """Only parse WH questions (no yes/no detection)."""
    text = text.lower().strip()
    tokens = tokenize(text)
    if not tokens:
        return None, None, None, None

    wh_words = {"what", "who", "where", "when", "why", "how"}
    first_word = tokens[0]

    if first_word in wh_words:
        if "color" in tokens:
            m = re.search(r"what color is (.+)", text)
            if m:
                subject = remove_articles(m.group(1).strip())
                return "wh", subject, "has_color", None
        else:
            m = re.search(r"what is (.+)", text)
            if m:
                subject = remove_articles(m.group(1).strip())
                return "wh", subject, None, None
        return "wh", None, None, None

    else:
        return None, None, None, None

# Improved phrase matching using fuzzywuzzy

recent_phrase_responses = []

def answer_phrase(kb, user_input, threshold=75):
    # Build a list of (trigger_phrase, response_list) tuples
    trigger_response_pairs = []
    for entry in kb["knowledge"]:
        if entry["type"] == "phrase":
            # Gather all trigger phrases for this entry
            triggers = []
            if isinstance(entry.get("inputs"), list):
                triggers = entry["inputs"]
            elif "input" in entry:
                triggers = [entry["input"]]

            for trig in triggers:
                trigger_response_pairs.append((trig, entry["outputs"]))

    # Use fuzzy matching to find best trigger phrase match
    triggers = [trig for trig, _ in trigger_response_pairs]
    if not triggers:
        return None

    best_match, score = process.extractOne(user_input, triggers, scorer=fuzz.token_sort_ratio)
    if score < threshold:
        return None

    # Find the outputs linked to the best match trigger
    for trig, outputs in trigger_response_pairs:
        if trig == best_match:
            # Return first output not recently used, else first output
            for resp in outputs:
                if resp not in recent_phrase_responses:
                    recent_phrase_responses.append(resp)
                    if len(recent_phrase_responses) > 5:
                        recent_phrase_responses.pop(0)
                    return resp
            return outputs[0]  # fallback if all outputs are recent

    return None

def answer_example(kb, user_input, threshold=0.7):
    user_input_lower = user_input.lower().strip()

    for entry in kb["knowledge"]:
        if entry["type"] == "example" and user_input_lower == entry["input"]:
            return entry["output"]

    example_inputs = [entry["input"] for entry in kb["knowledge"] if entry["type"] == "example"]
    matches = difflib.get_close_matches(user_input_lower, example_inputs, n=1, cutoff=threshold)
    if matches:
        match = matches[0]
        for entry in kb["knowledge"]:
            if entry["type"] == "example" and entry["input"] == match:
                return entry["output"]

    return None

def answer_question(kb, input_text):
    math_result = try_math(input_text)
    if math_result is not None:
        return math_result

    phrase_answer = answer_phrase(kb, input_text)
    if phrase_answer:
        return phrase_answer

    example_answer = answer_example(kb, input_text)
    if example_answer:
        return example_answer

    qtype, subject, predicate, obj = parse_question(input_text)

    if qtype == "wh":
        if subject is None:
            facts = find_facts_or_concepts(kb, input_text)
            return facts[0] if facts else "I don't know."
        if predicate:
            matches = find_association(kb, subject=subject, predicate=predicate)
            return matches[0]["object"] if matches else f"I don't know the {predicate.replace('_', ' ')} of {subject}."
        else:
            facts = []
            subject_tokens = set(tokenize(subject))
            for entry in kb["knowledge"]:
                if entry["type"] in ["fact", "concept"]:
                    content_tokens = set(tokenize(entry["content"].lower()))
                    if subject_tokens & content_tokens:
                        facts.append(entry["content"])
            return facts[0] if facts else f"I don't know about {subject}."

    facts = find_facts_or_concepts(kb, input_text)
    return facts[0] if facts else "I don't know the answer to that."

def add_or_merge_entry(kb, new_entry):
    t = new_entry["type"]

    if t == "association":
        for entry in kb["knowledge"]:
            if entry["type"] == "association" and entry["content"] == new_entry["content"]:
                return
        kb["knowledge"].append(new_entry)
        return

    if t == "phrase":
        for entry in kb["knowledge"]:
            if entry["type"] == "phrase":
                existing_triggers = set()
                if isinstance(entry.get("inputs"), list):
                    existing_triggers.update(entry["inputs"])
                elif "input" in entry:
                    existing_triggers.add(entry["input"])
                if set(new_entry.get("inputs", [])) & existing_triggers:
                    for output in new_entry["outputs"]:
                        if output not in entry["outputs"]:
                            entry["outputs"].append(output)
                    return
        kb["knowledge"].append(new_entry)
        return

    if t == "example":
        for entry in kb["knowledge"]:
            if entry["type"] == "example" and entry["input"] == new_entry["input"]:
                entry["output"] = new_entry["output"]
                return
        kb["knowledge"].append(new_entry)
        return

    if t in ["fact", "concept", "rule", "instruction"]:
        for entry in kb["knowledge"]:
            if entry["type"] == t and entry["content"].lower() == new_entry["content"].lower():
                return
        kb["knowledge"].append(new_entry)
        return

def training_session(kb):
    print("Entered training mode. Type 'exit train' to leave.")
    while True:
        t = input("Type? (fact/rule/example/instruction/concept/association/phrase): ").strip().lower()
        if t == "exit train":
            print("Exiting training mode.")
            break
        if t not in ["fact", "rule", "example", "instruction", "concept", "association", "phrase"]:
            print("Unknown type, try again.")
            continue

        if t == "association":
            subject = input("Enter association subject: ").strip().lower()
            predicate = input("Enter association predicate: ").strip().lower()
            obj = input("Enter association object: ").strip().lower()
            add_or_merge_entry(kb, {
                "type": "association",
                "content": {"subject": subject, "predicate": predicate, "object": obj}
            })
            print("Association added/merged.")

        elif t == "example":
            example_input = input("Enter example input sentence: ").strip().lower()
            example_output = input("Enter example output (answer): ").strip()
            add_or_merge_entry(kb, {
                "type": "example",
                "input": example_input,
                "output": example_output
            })
            print("Example added/merged.")

        elif t == "phrase":
            triggers = []
            while True:
                trig = input("Enter a trigger phrase/word (or press enter to finish): ").strip().lower()
                if not trig:
                    break
                triggers.append(trig)

            outputs = []
            while True:
                out = input("Enter a possible response (or press enter to finish): ").strip()
                if not out:
                    break
                outputs.append(out)

            if triggers and outputs:
                add_or_merge_entry(kb, {
                    "type": "phrase",
                    "inputs": triggers,
                    "outputs": outputs
                })
                print("Phrase added/merged.")
            else:
                print("No triggers or outputs entered. Phrase not saved.")

        else:
            content = input(f"Enter the {t} content: ").strip()
            add_or_merge_entry(kb, {
                "type": t,
                "content": content
            })
            print(f"{t.capitalize()} added/merged.")

        save_knowledge(kb)

def main():
    kb = load_knowledge()
    print("Chatbot ready! Type 'train' to add knowledge, 'exit' to quit.")
    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        if user_input.lower() == "exit":
            print("Goodbye!")
            break
        elif user_input.lower() == "train":
            training_session(kb)
        else:
            response = answer_question(kb, user_input)
            print("Bot:", response)

if __name__ == "__main__":
    main()
