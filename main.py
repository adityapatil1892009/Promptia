
import os
import uuid
import tempfile
import logging

from flask import Flask, render_template, render_template_string, request
from dotenv import load_dotenv
from supabase import create_client

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = "https://pcbegtsfvaizlhnhwexa.supabase.co"
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")

if not SUPABASE_SECRET_KEY:
    raise RuntimeError("SUPABASE_SECRET_KEY is missing.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

BUCKET_NAME = "images"

REFERENCE_IMAGE = os.path.join(
    BASE_DIR, "static", "images", "img.jpeg"
)

COMPARISON_URL = (
    "https://www.imgonline.com.ua/eng/similarity-percent.php"
)

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MANUAL_IMAGE = (
    "MANUAL REVIEW REQUIRED: Image comparison failed."
)


# --------------------------------------------------
# RESPONSE PAGE
# --------------------------------------------------

def message_page(title, message, status=200):
    return render_template_string(
        """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport"
                  content="width=device-width, initial-scale=1.0">
            <title>{{ title }} - Promptia</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    max-width: 650px;
                    margin: 70px auto;
                    padding: 20px;
                    line-height: 1.6;
                    color: #222;
                }
                h1 { color: #173b68; }
                a { color: #173b68; }
            </style>
        </head>
        <body>
            <h1>{{ title }}</h1>
            <p>{{ message }}</p>
            <a href="/">Return to Promptia</a>
        </body>
        </html>
        """,
        title=title,
        message=message,
    ), status


# --------------------------------------------------
# SELENIUM BROWSER
# --------------------------------------------------

def create_browser():
    options = webdriver.ChromeOptions()

    options.page_load_strategy = "eager"

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1365,1000")
    options.add_argument("--disable-extensions")

    chrome_binary = os.environ.get("CHROME_BIN")
    driver_path = os.environ.get("CHROMEDRIVER_PATH")

    if chrome_binary:
        options.binary_location = chrome_binary

    if driver_path:
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(
            service=service,
            options=options,
        )
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)

    return driver


def close_browser(driver):
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            app.logger.exception("Could not close Selenium browser.")


# --------------------------------------------------
# IMAGE COMPARISON
# --------------------------------------------------

def compare_images(uploaded_image_path):
    driver = None

    try:
        if not os.path.isfile(REFERENCE_IMAGE):
            app.logger.error("Reference image is missing.")
            return MANUAL_IMAGE

        if not os.path.isfile(uploaded_image_path):
            app.logger.error("Uploaded temporary image is missing.")
            return MANUAL_IMAGE

        app.logger.info("IMAGE CHECK: Starting comparison.")

        driver = create_browser()
        wait = WebDriverWait(driver, 30)

        app.logger.info("IMAGE CHECK: Opening ImgOnline.")
        driver.get(COMPARISON_URL)

        reference_input = wait.until(
            EC.presence_of_element_located(
                (By.NAME, "uploadfile")
            )
        )

        uploaded_input = wait.until(
            EC.presence_of_element_located(
                (By.NAME, "uploadfile2")
            )
        )

        app.logger.info("IMAGE CHECK: Uploading reference image.")
        reference_input.send_keys(REFERENCE_IMAGE)

        app.logger.info("IMAGE CHECK: Uploading participant image.")
        uploaded_input.send_keys(uploaded_image_path)

        submit_button = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    '//*[@id="content"]/form/div[3]/input',
                )
            )
        )

        app.logger.info("IMAGE CHECK: Submitting for comparison.")

        driver.execute_script(
            "arguments[0].click();",
            submit_button,
        )

        # Wait until the actual result page appears.
        wait.until(
            EC.url_contains("similarity-percent-result.php")
        )

        app.logger.info(
            "IMAGE CHECK: Result page detected; waiting for result."
        )

        result_element = wait.until(
            EC.visibility_of_element_located(
                (By.XPATH, '//*[@id="content"]/span')
            )
        )

        result = result_element.text.strip()

        if not result:
            app.logger.error("IMAGE CHECK: Result was empty.")
            return MANUAL_IMAGE

        app.logger.info(
            "IMAGE CHECK: Final result received: %s",
            result,
        )

        return result

    except Exception:
        app.logger.exception(
            "IMAGE CHECK: Failed; manual review is required."
        )
        return MANUAL_IMAGE

    finally:
        close_browser(driver)


# --------------------------------------------------
# HOME PAGE
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


# --------------------------------------------------
# SUBMISSION
# --------------------------------------------------

@app.route("/sub_1", methods=["GET", "POST"])
def submit():

    if request.method == "GET":
        return render_template("index.html")

    temporary_path = None

    try:
        name = request.form.get("name", "").strip()
        enr = request.form.get("enr", "").strip()
        prompt = request.form.get("prompt", "").strip()
        image_file = request.files.get("image")

        # Validate form fields.
        if not name or not enr or not prompt:
            return message_page(
                "Missing information",
                "Please fill in your name, enrollment number, "
                "and master prompt.",
                400,
            )

        if len(name) > 100 or len(enr) > 50 or len(prompt) > 10000:
            return message_page(
                "Invalid information",
                "Please check the length of your submitted fields.",
                400,
            )

        if not image_file or not image_file.filename:
            return message_page(
                "Image required",
                "Please select an image to submit.",
                400,
            )

        content_type = (
            image_file.mimetype or ""
        ).lower().split(";")[0].strip()

        if content_type not in ALLOWED_TYPES:
            return message_page(
                "Unsupported image",
                "Upload a PNG, JPEG, or WebP image.",
                400,
            )

        image_bytes = image_file.read()

        if not image_bytes:
            return message_page(
                "Empty image",
                "The selected image is empty.",
                400,
            )

        if len(image_bytes) > 10 * 1024 * 1024:
            return message_page(
                "Image too large",
                "Your image must be 10 MB or smaller.",
                400,
            )

        extension = ALLOWED_TYPES[content_type]

        with tempfile.NamedTemporaryFile(
            suffix=extension,
            delete=False,
        ) as temporary_file:
            temporary_file.write(image_bytes)
            temporary_path = temporary_file.name

        image_path = (
            f"submissions/{uuid.uuid4().hex}{extension}"
        )

        # Step 1: Upload the image to Supabase Storage.
        storage_uploaded = False

        try:
            app.logger.info("STORAGE: Uploading submitted image.")

            supabase.storage.from_(BUCKET_NAME).upload(
                image_path,
                image_bytes,
                file_options={
                    "content-type": content_type,
                    "upsert": "false",
                },
            )

            storage_uploaded = True
            app.logger.info("STORAGE: Image upload completed.")

        except Exception:
            app.logger.exception("STORAGE: Image upload failed.")

        # Step 2: Wait for the image checker to return its result.
        # This call must finish before the database insert below.
        app.logger.info(
            "SUBMISSION: Waiting for image comparison to finish."
        )

        similarity_result = compare_images(temporary_path)

        # Step 3: Save the submission only after comparison finishes.
        # The master prompt is stored but is not evaluated.
        app.logger.info(
            "DATABASE: Saving submission and final comparison result."
        )

        insert_data = {
            "name": name,
            "enr": enr,
            "prompt": prompt,
            "image_path": image_path,
            "similarity_result": similarity_result,
        }

        if not storage_uploaded:
            app.logger.warning(
                "DATABASE: Storage upload failed; the image_path "
                "may not refer to an uploaded object."
            )

        try:
            response = (
                supabase.table("promptia_submissions")
                .insert(insert_data)
                .execute()
            )

            app.logger.info("DATABASE RESPONSE: %s", response.data)

            if not response.data:
                raise RuntimeError(
                    "Supabase returned no inserted row."
                )

        except Exception as db_error:
            app.logger.exception(
                "DATABASE INSERT FAILED: %s",
                str(db_error),
            )
            raise

        app.logger.info(
            "DATABASE: Submission and result saved successfully."
        )

        # Step 4: Only now return the final page to the participant.
        if similarity_result.startswith("MANUAL REVIEW REQUIRED"):
            return message_page(
                "Submission received",
                "Your submission has been saved. Automatic image "
                "comparison could not be completed, so the image "
                "requires manual review. Please do not submit again.",
            )

        return message_page(
            "Submission successful",
            "Your image comparison is complete and your submission "
            "and result have been saved. Thank you for participating!",
        )

    except Exception:
        app.logger.exception("SUBMISSION: Processing failed.")

        return message_page(
            "Submission processing failed",
            "We could not finish saving your submission. Please "
            "contact the organizers before submitting again.",
            500,
        )

    finally:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                app.logger.exception(
                    "Could not remove temporary image."
                )


# --------------------------------------------------
# FILE SIZE ERROR
# --------------------------------------------------

@app.errorhandler(413)
def file_too_large(error):
    return message_page(
        "Image too large",
        "The request exceeds the 10 MB upload limit.",
        413,
    )


# --------------------------------------------------
# RUN APPLICATION
# --------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
