import re
with open("generate_summary.py", "r") as f:
    content = f.read()

mock_text = """        return "<p><strong>You've got this!</strong> Week 2 is a busy one with 15 total assignments across Data Science Programming and Data Wrangling & Visualization. You'll be tackling new concepts in R, exploring data, and submitting your course goals. <ul><li>DS 250: Focus on Checkpoints and Quizzes.</li><li>DS 350: Dive into Case Studies and your first Tasks.</li></ul> Have a great week!</p>" """
content = content.replace('print("  ⚠️  No valid GEMINI_API_KEY found, skipping AI summary.")\n        return None', f'print("  ⚠️  No valid GEMINI_API_KEY found, using MOCK summary.")\n{mock_text}')

with open("generate_summary.py", "w") as f:
    f.write(content)
