from ollama import chat

MODEL = "qwen2.5:3b"


def ask(prompt):

    response = chat(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return response["message"]["content"]