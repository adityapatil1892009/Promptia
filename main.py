
from flask import Flask, render_template, request
from dotenv import load_dotenv
import os
import uuid
from supabase import create_client

load_dotenv()

app = Flask(__name__, template_folder=".")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SECRET_KEY"]
)

ALLOWED_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp"
}


@app.route("/")
def index():
    return render_template("templates/index.html")


@app.route("/sub_1", methods=["GET", "POST"])
def sub_1():

    if request.method == "GET":
        return render_template("templates/index.html")

    name = request.form.get("name", "").strip()
    enr = request.form.get("enr", "").strip()
    prompt = request.form.get("prompt", "").strip()
    image = request.files.get("image")

    if not name or not enr or not prompt or not image:
        return "Please complete all fields.", 400

    if len(name) > 100 or len(enr) > 50 or len(prompt) > 10000:
        return "One or more fields exceed the allowed length.", 400

    extension = ALLOWED_TYPES.get(image.mimetype)

    if not extension:
        return "Upload a PNG, JPG or WebP image.", 400

    image_data = image.read()

    if not image_data or len(image_data) > 10 * 1024 * 1024:
        return "Image must be between 1 byte and 10 MB.", 400

    image_path = f"{uuid.uuid4().hex}.{extension}"

    try:
        # Upload image to Supabase Storage
        supabase.storage.from_("images").upload(
            image_path,
            image_data,
            {
                "content-type": image.mimetype,
                "upsert": "false"
            }
        )

        # Save submission details in the database
        supabase.table("promptia_submissions").insert({
            "name": name,
            "enr": enr,
            "prompt": prompt,
            "image_path": image_path
        }).execute()

        return """
            <h2>Submission received!</h2>
            <p>Your Promptia entry has been saved.</p>
            <a href="/">Back to form</a>
        """

    except Exception:
        app.logger.exception("Promptia submission failed")
        return "Submission failed. Please contact the organizer.", 500


@app.errorhandler(413)
def file_too_large(error):
    return "Image exceeds the 10 MB limit.", 413


if __name__ == "__main__":
    app.run(debug=True)
