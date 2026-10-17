
import os
import time
import uuid
import tempfile

from flask import Flask, render_template, request
from dotenv import load_dotenv
from supabase import create_client

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SECRET_KEY"]
)

ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp"
}

REFERENCE_IMAGE = os.path.join(
    BASE_DIR, "static", "images", "img.jpeg"
)

COMPARISON_URL = (
    "https://www.imgonline.com.ua/eng/similarity-percent.php"
)

PROMPT_EVALUATOR_URL = (
    "https://www.spaceprompts.com/ai-tools/prompt-evaluator"
)

PROMPT_INPUT_CLASS = "prompt-evaluator_promptTextarea__BwMQ0"
PROMPT_BUTTON_CLASS = "prompt-evaluator_evaluateButton__5l8es"

PROMPT_RESULT_XPATH = (
    "/html/body/main/section/div/div[2]/div/div[2]/"
    "div/div[1]/div[1]/span[1]"
)

MANUAL_IMAGE = "MANUAL REVIEW REQUIRED: Image comparison failed."
MANUAL_PROMPT = "MANUAL REVIEW REQUIRED: Prompt evaluation failed."


def create_browser():
    options = webdriver.ChromeOptions()

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1365,1000")

    # Use the browser and driver installed on the hosting server.
    chrome_binary = os.environ.get("CHROME_BIN")
    driver_path = os.environ.get("CHROMEDRIVER_PATH")

    if chrome_binary:
        options.binary_location = chrome_binary

    if driver_path:
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(
            service=service,
            options=options
        )
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)
    return driver


def close_browser(driver):
    if driver:
        try:
            driver.quit()
        except Exception:
            app.logger.exception("Could not close browser")


def compare_images(temp_path):
    driver = None

    try:
        if not os.path.isfile(REFERENCE_IMAGE):
            app.logger.error(
                "Reference image missing: %s", REFERENCE_IMAGE
            )
            return "MANUAL REVIEW REQUIRED: Reference image missing."

        driver = create_browser()
        wait = WebDriverWait(driver, 20)

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

        reference_input.send_keys(REFERENCE_IMAGE)
        uploaded_input.send_keys(temp_path)

        submit_button = wait.until(
            EC.element_to_be_clickable((
                By.XPATH,
                '//*[@id="content"]/form/div[3]/input'
            ))
        )

        submit_button.click()

        wait.until(
            EC.url_contains("similarity-percent-result.php")
        )

        result_element = wait.until(
            EC.visibility_of_element_located(
                (By.XPATH, '//*[@id="content"]/span')
            )
        )

        result = result_element.text.strip()

        if not result:
            raise ValueError("Image comparison returned empty text")

        app.logger.info("Image comparison completed")
        return result

    except Exception:
        app.logger.exception("Image comparison failed")
        return MANUAL_IMAGE

    finally:
        close_browser(driver)




def evaluate_prompt(prompt):
    driver = None

    try:
        app.logger.info("PROMPT: Starting evaluation")

        driver = create_browser()
        wait = WebDriverWait(driver, 30)

        driver.get(PROMPT_EVALUATOR_URL)

        # Wait for the prompt input, not the Evaluate button.
        prompt_box = wait.until(
            EC.visibility_of_element_located(
                (By.CLASS_NAME, PROMPT_INPUT_CLASS)
            )
        )

        # Paste the participant's prompt.
        prompt_box.clear()
        prompt_box.send_keys(prompt)

        app.logger.info("PROMPT: Prompt entered")

        # Find the button without waiting for it to become clickable.
        button = driver.find_element(
            By.CLASS_NAME,
            PROMPT_BUTTON_CLASS
        )

        # Click Evaluate immediately.
        driver.execute_script("arguments[0].click();", button)

        app.logger.info("PROMPT: Evaluate clicked; waiting 5 seconds")

        # Allow the evaluator to generate its result.
        time.sleep(5)

        # Read the result.
        result_element = wait.until(
            EC.visibility_of_element_located(
                (By.XPATH, PROMPT_RESULT_XPATH)
            )
        )

        result = result_element.text.strip()

        if not result:
            app.logger.warning("PROMPT: Result was empty")
            return (
                "MANUAL REVIEW REQUIRED: Evaluator returned an empty result."
            )

        app.logger.info("PROMPT: Evaluation result: %s", result[:300])

        return result

    except Exception as error:
        app.logger.exception(
            "PROMPT: Evaluation failed: %s",
            type(error).__name__
        )

        if driver:
            try:
                driver.save_screenshot(
                    os.path.join(BASE_DIR, "prompt_evaluator_failure.png")
                )
            except Exception:
                app.logger.exception("PROMPT: Could not save screenshot")

        return (
            "MANUAL REVIEW REQUIRED: Prompt evaluation failed: "
            + type(error).__name__
        )

    finally:
        close_browser(driver)




def save_result(submission_id, column, result):
    # Only allow updates to the two intended result columns.
    if column not in {
        "similarity_result",
        "prompt_evaluation_result"
    }:
        app.logger.error("Invalid result column: %s", column)
        return False

    try:
        supabase.table("promptia_submissions").update({
            column: result
        }).eq("id", submission_id).execute()

        app.logger.info(
            "Saved %s for submission %s",
            column, submission_id
        )
        return True

    except Exception:
        app.logger.exception(
            "Could not save %s for submission %s",
            column, submission_id
        )
        return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sub_1", methods=["GET", "POST"])
def sub_1():
    if request.method == "GET":
        return render_template("index.html")

    name = request.form.get("name", "").strip()
    enr = request.form.get("enr", "").strip()
    prompt = request.form.get("prompt", "").strip()
    image_file = request.files.get("image")

    if not name or not enr or not prompt or not image_file:
        return "Please complete all fields.", 400

    if (
        len(name) > 100
        or len(enr) > 50
        or len(prompt) > 10000
    ):
        return "One or more fields are too long.", 400

    extension = ALLOWED_TYPES.get(image_file.mimetype)

    if not extension:
        return "Upload a PNG, JPG or WebP image.", 400

    image_data = image_file.read()

    if not image_data or len(image_data) > 10 * 1024 * 1024:
        return "Image must be between 1 byte and 10 MB.", 400

    image_path = f"{uuid.uuid4().hex}{extension}"
    temp_path = None
    submission_id = None

    try:
        # Create the temporary image used by ImgOnline.
        with tempfile.NamedTemporaryFile(
            suffix=extension,
            delete=False
        ) as temp_file:
            temp_file.write(image_data)
            temp_path = temp_file.name

        # STEP 1: SAVE THE SUBMISSION FIRST.
        # Evaluator failures must not prevent this database insert.
        inserted = supabase.table(
            "promptia_submissions"
        ).insert({
            "name": name,
            "enr": enr,
            "prompt": prompt,
            "image_path": image_path,
            "similarity_result": "Pending - manual review if not updated",
            "prompt_evaluation_result": (
                "Pending - manual review if not updated"
            )
        }).execute()

        if not inserted.data:
            raise RuntimeError("Database insert returned no data")

        submission_id = inserted.data[0]["id"]

        app.logger.info(
            "Submission saved before evaluation: %s",
            submission_id
        )

        # STEP 2: TRY TO STORE THE IMAGE.
        # If storage fails, the database submission still exists.
        storage_ok = False

        try:
            supabase.storage.from_("images").upload(
                image_path,
                image_data,
                {
                    "content-type": image_file.mimetype,
                    "upsert": "false"
                }
            )
            storage_ok = True

        except Exception:
            app.logger.exception(
                "Image storage upload failed for submission %s",
                submission_id
            )

        # STEP 3: RUN IMAGE COMPARISON INDEPENDENTLY.
        # A failed comparison becomes a manual-review status.
        if storage_ok:
            similarity_result = compare_images(temp_path)
        else:
            similarity_result = (
                "MANUAL REVIEW REQUIRED: Image storage upload failed."
            )

        save_result(
            submission_id,
            "similarity_result",
            similarity_result
        )

        # STEP 4: RUN PROMPT EVALUATION REGARDLESS OF IMAGE RESULT.
        prompt_result = evaluate_prompt(prompt)

        save_result(
            submission_id,
            "prompt_evaluation_result",
            prompt_result
        )

        # STEP 5: DO NOT REJECT THE SUBMISSION BECAUSE AN EVALUATOR
        # FAILED. The submission was saved at STEP 1.
        return """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport"
                  content="width=device-width, initial-scale=1.0">
            <title>Promptia - Submission Received</title>
        </head>
        <body>
            <h2>Submission received!</h2>
            <p>Your submission has been recorded.</p>
            <p>If an automatic evaluation fails, it will need
               manual review by the organizers.</p>
            <a href="/">Back to form</a>
        </body>
        </html>
        """

    except Exception:
        app.logger.exception("Promptia submission failed")

        if submission_id is not None:
            # The row was already created. Do not tell the participant
            # that it was never saved merely because later work failed.
            return """
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport"
                      content="width=device-width, initial-scale=1.0">
                <title>Promptia - Submission Recorded</title>
            </head>
            <body>
                <h2>Your submission has been recorded.</h2>
                <p>Automatic processing was interrupted.
                   The organizers may need to review it manually.</p>
                <a href="/">Back to form</a>
            </body>
            </html>
            """

        # No database row was confirmed, so don't falsely claim success.
        return (
            "We could not confirm that your submission was saved. "
            "Please contact the organizers before submitting again.",
            500
        )

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                app.logger.exception(
                    "Could not remove temporary image"
                )


@app.errorhandler(413)
def file_too_large(error):
    return "Image exceeds the 10 MB limit.", 413


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
