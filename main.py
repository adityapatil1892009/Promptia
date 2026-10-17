
import os
import uuid
import tempfile
import logging

from flask import (
    Flask,
    render_template,
    render_template_string,
    request,
)
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
    raise RuntimeError(
        "SUPABASE_SECRET_KEY is missing. "
        "Add it to your .env file or deployment environment variables."
    )

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
# HTML RESPONSE HELPERS
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

    # Do not wait for the entire page to finish loading.
    # Explicit waits below will wait for the required elements.
    options.page_load_strategy = "none"

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
    driver.set_script_timeout(60)

    return driver


def close_browser(driver):
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            app.logger.exception(
                "Could not close the Selenium browser cleanly."
            )


# --------------------------------------------------
# IMAGE SIMILARITY COMPARISON
# --------------------------------------------------

def compare_images(uploaded_image_path):
    driver = None

    try:
        if not os.path.isfile(REFERENCE_IMAGE):
            app.logger.error(
                "Reference image not found: %s",
                REFERENCE_IMAGE,
            )
            return MANUAL_IMAGE

        if not os.path.isfile(uploaded_image_path):
            app.logger.error("Uploaded temporary image not found.")
            return MANUAL_IMAGE

        app.logger.info("Starting image comparison.")

        driver = create_browser()
        wait = WebDriverWait(driver, 30)

        app.logger.info("Opening ImgOnline.")
        driver.get(COMPARISON_URL)

        # Wait for both image upload fields.
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

        app.logger.info("Uploading the reference image.")
        reference_input.send_keys(REFERENCE_IMAGE)

        app.logger.info("Uploading the submitted image.")
        uploaded_input.send_keys(uploaded_image_path)

        # Locate the comparison submit button.
        submit_button = wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    '//*[@id="content"]/form/div[3]/input',
                )
            )
        )

        app.logger.info(
            "Submitting images to ImgOnline."
        )

        # JavaScript click avoids some native Selenium click issues.
        driver.execute_script(
            "arguments[0].click();",
            submit_button,
        )

        app.logger.info(
            "Submit click triggered. Waiting for the result."
        )

        # Wait for ImgOnline's result page.
        wait.until(
            EC.url_contains("similarity-percent-result.php")
        )

        app.logger.info(
            "ImgOnline result page detected."
        )

        result_element = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, '//*[@id="content"]/span')
            )
        )

        result = result_element.text.strip()

        if not result:
            app.logger.error(
                "ImgOnline returned an empty similarity result."
            )
            return MANUAL_IMAGE

        app.logger.info(
            "Image comparison completed: %s",
            result,
        )

        return result

    except Exception:
        app.logger.exception(
            "Image comparison failed. Manual review is required."
        )
        return MANUAL_IMAGE

    finally:
        close_browser(driver)


# --------------------------------------------------
# SAVE SIMILARITY RESULT
# --------------------------------------------------

def save_result(submission_id, result):
    try:
        response = (
            supabase.table("promptia_submissions")
            .update({"similarity_result": result})
            .eq("id", submission_id)
            .execute()
        )

        if not response.data:
            app.logger.warning(
                "No submission row was updated for ID %s.",
                submission_id,
            )
            return False

        app.logger.info(
            "Similarity result saved for submission ID %s.",
            submission_id,
        )

        return True

    except Exception:
        app.logger.exception(
            "Could not save the similarity result for ID %s.",
            submission_id,
        )
        return False


# --------------------------------------------------
# HOME PAGE
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


# --------------------------------------------------
# SUBMISSION ROUTE
# --------------------------------------------------

@app.route("/sub_1", methods=["GET", "POST"])
def submit():

    if request.method == "GET":
        return render_template("index.html")

    submission_id = None
    temporary_path = None

    try:
        # Read form fields.
        name = request.form.get("name", "").strip()
        enr = request.form.get("enr", "").strip()
        prompt = request.form.get("prompt", "").strip()

        image_file = request.files.get("image")

        # Validate required fields.
        if not name or not enr or not prompt:
            return message_page(
                "Missing information",
                "Please fill in your name, enrollment number, "
                "and master prompt.",
                400,
            )

        if len(name) > 100:
            return message_page(
                "Invalid name",
                "Your name must be 100 characters or fewer.",
                400,
            )

        if len(enr) > 50:
            return message_page(
                "Invalid enrollment number",
                "Your enrollment number must be 50 characters "
                "or fewer.",
                400,
            )

        if len(prompt) > 10000:
            return message_page(
                "Prompt too long",
                "Your master prompt must be 10,000 characters "
                "or fewer.",
                400,
            )

        if not image_file or not image_file.filename:
            return message_page(
                "Image required",
                "Please select the image you created.",
                400,
            )

        content_type = (
            image_file.mimetype or ""
        ).lower().split(";")[0].strip()

        if content_type not in ALLOWED_TYPES:
            return message_page(
                "Unsupported image",
                "Please upload a PNG, JPEG, or WebP image.",
                400,
            )

        image_bytes = image_file.read()

        if not image_bytes:
            return message_page(
                "Empty image",
                "The selected image is empty. Please choose "
                "another image.",
                400,
            )

        if len(image_bytes) > 10 * 1024 * 1024:
            return message_page(
                "Image too large",
                "Your image must be 10 MB or smaller.",
                400,
            )

        extension = ALLOWED_TYPES[content_type]

        # Prepare a temporary file for Selenium.
        with tempfile.NamedTemporaryFile(
            suffix=extension,
            delete=False,
        ) as temporary_file:
            temporary_file.write(image_bytes)
            temporary_path = temporary_file.name

        # Generate a unique path for Supabase Storage.
        image_path = (
            f"submissions/{uuid.uuid4().hex}{extension}"
        )

        # Save the submission first so that the participant's
        # information is retained even if comparison fails.
        app.logger.info(
            "Saving submission for enrollment number: %s",
            enr,
        )

        insert_response = (
            supabase.table("promptia_submissions")
            .insert({
                "name": name,
                "enr": enr,
                "prompt": prompt,
                "image_path": image_path,
                "similarity_result": (
                    "Pending - image comparison not completed"
                ),
            })
            .execute()
        )

        if not insert_response.data:
            raise RuntimeError(
                "Supabase did not confirm that the submission "
                "was saved."
            )

        submission_id = insert_response.data[0]["id"]

        app.logger.info(
            "Submission saved. ID: %s",
            submission_id,
        )

        # Upload the submitted image to Supabase Storage.
        upload_succeeded = False

        try:
            supabase.storage.from_(BUCKET_NAME).upload(
                image_path,
                image_bytes,
                file_options={
                    "content-type": content_type,
                    "upsert": "false",
                },
            )

            upload_succeeded = True

            app.logger.info(
                "Image uploaded to Supabase Storage: %s",
                image_path,
            )

        except Exception:
            app.logger.exception(
                "Storage upload failed for submission ID %s.",
                submission_id,
            )

        # Compare images only if the image was stored successfully.
        if upload_succeeded:
            similarity_result = compare_images(
                temporary_path
            )
        else:
            similarity_result = (
                "MANUAL REVIEW REQUIRED: "
                "Image upload failed."
            )

        # Save the comparison result without evaluating the prompt.
        result_saved = save_result(
            submission_id,
            similarity_result,
        )

        if not result_saved:
            app.logger.warning(
                "Submission %s is saved, but its final result "
                "could not be updated.",
                submission_id,
            )

        if similarity_result.startswith(
            "MANUAL REVIEW REQUIRED"
        ):
            return message_page(
                "Submission received",
                "Your submission has been recorded. Automatic "
                "image comparison could not be completed, so "
                "the image needs manual review. Please do not "
                "submit it again.",
            )

        return message_page(
            "Submission successful",
            "Your submission has been recorded and the image "
            "comparison has been completed. Thank you for "
            "participating!",
        )

    except Exception:
        app.logger.exception(
            "Submission processing failed."
        )

        if submission_id is not None:
            return message_page(
                "Submission recorded",
                "Your submission was saved, but processing did "
                "not finish successfully. The organizers can "
                "review it. Please do not submit it again.",
                200,
            )

        return message_page(
            "Submission failed",
            "We could not confirm that your submission was "
            "saved. Please contact the organizers before "
            "trying again.",
            500,
        )

    finally:
        # Remove the temporary local image.
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                app.logger.exception(
                    "Could not remove the temporary image."
                )


# --------------------------------------------------
# FILE TOO LARGE
# --------------------------------------------------

@app.errorhandler(413)
def file_too_large(error):
    return message_page(
        "Image too large",
        "The submitted request exceeds the 10 MB limit.",
        413,
    )


# --------------------------------------------------
# START APPLICATION
# --------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
