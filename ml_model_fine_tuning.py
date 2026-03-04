"""
Sentinel-D DistilBERT Intent Classifier Fine-Tuning Pipeline (v2)
Enhanced for Class Imbalance Mitigation & Robust Learning

Phases:
1. Data Acquisition with Dynamic Class Capping (Stack Overflow Scraper)
2. Auto-Annotation (Teacher Model Distillation with BART)
3. Strict Class Imbalance Handling & Preprocessing
4. Lightweight Learning Rate Search
5. Fine-Tuning with Weighted Cross Entropy Loss
6. Evaluation (Metrics & Confusion Matrix)
7. Export & Packaging (Model Artifacts)
"""
#!pip install transformers datasets scikit-learn seaborn pandas torch beautifulsoup4 imbalanced-learn requests

import os
import json
import shutil
import logging
import time
import requests
from typing import Dict, List, Tuple, Any, Optional
from datetime import datetime
from collections import Counter

from bs4 import BeautifulSoup
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score
from imblearn.over_sampling import RandomOverSampler

import torch
import torch.nn as nn
from transformers import (
    pipeline,
    DistilBertTokenizer,
    DistilBertForSequenceClassification,
    Trainer,
    TrainingArguments,
    EarlyStoppingCallback,
)
from datasets import Dataset

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS & CONFIGURATION
# ============================================================================
INTENT_CLASSES = [
    "VERSION_PIN",
    "API_MIGRATION",
    "MONKEY_PATCH",
    "FULL_REFACTOR",
]

LABEL_TO_ID = {label: idx for idx, label in enumerate(INTENT_CLASSES)}
ID_TO_LABEL = {idx: label for label, idx in LABEL_TO_ID.items()}

# Stack Overflow configuration
SO_TAGS = ["npm", "maven", "python", "reactjs", "security"]
PAGES_PER_TAG = 2
PAGESIZE = 100
MIN_TEXT_LENGTH = 50

# Class imbalance mitigation
CLASS_CAP_PER_CLASS = 300  # Maximum samples per class
STRICT_MAX_DIFF = 30  # Max difference between any two classes
CLASS_SPECIFIC_KEYWORDS = {
    "VERSION_PIN": ["version", "pin", "lock", "freeze", "dependency"],
    "API_MIGRATION": ["api", "endpoint", "migrate", "upgrade", "v2", "v3"],
    "MONKEY_PATCH": ["workaround", "patch", "hotfix", "temporary", "quick fix"],
    "FULL_REFACTOR": ["refactor", "rewrite", "redesign", "architecture", "restructure"],
}

# Model & training configuration
MODEL_NAME = "distilbert-base-uncased"
OUTPUT_DIR = "distilbert-intent-classifier-v1"
DATASET_PATH = "intent_dataset.jsonl"
CONFUSION_MATRIX_PATH = "confusion_matrix.png"

# Training hyperparameters (optimized for Kaggle T4)
BATCH_SIZE_TRAIN = 16
BATCH_SIZE_EVAL = 32
NUM_EPOCHS = 6  # Increased to ensure convergence
LEARNING_RATE = 2e-5  # Will be tuned dynamically
WARMUP_STEPS = 200
WEIGHT_DECAY = 0.01
EVAL_STRATEGY = "epoch"  # Using correct syntax
SAVE_STRATEGY = "epoch"

# Learning rate search config
LR_SEARCH_RATES = [1e-5, 3e-5, 5e-5]
LR_SEARCH_EPOCHS = 1

# Test set size constraint
TEST_SET_SIZE = 180
TRAIN_RATIO = 0.7
EVAL_RATIO = 0.15
TEST_RATIO = 0.15

# Device configuration
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
logger.info(f"Using device: {DEVICE}")


# ============================================================================
# PHASE 1: DATA ACQUISITION WITH DYNAMIC CLASS CAPPING
# ============================================================================
def scrape_stackoverflow_posts() -> pd.DataFrame:
    """
    Scrape posts from Stack Overflow with dynamic class capping.
    
    Two-pass approach:
    - Pass 1: Generic tags for baseline data
    - Pass 2: Targeted keywords for minority classes
    
    Returns:
        DataFrame with columns: [text, source, title, label]
    """
    logger.info("=" * 70)
    logger.info("PHASE 1: DATA ACQUISITION (STACK OVERFLOW SCRAPER WITH DYNAMIC CLASS CAPPING)")
    logger.info("=" * 70)
    
    BASE_URL = "https://api.stackexchange.com/2.3/search/advanced"
    posts_data = []
    seen_titles = set()
    
    # PASS 1: Generic tags
    logger.info("PASS 1: Scraping generic Stack Overflow tags")
    for tag in SO_TAGS:
        logger.info(f"Scraping Stack Overflow tag: {tag}")
        
        for page in range(1, PAGES_PER_TAG + 1):
            try:
                params = {
                    "q": tag,
                    "tagged": tag,
                    "sort": "votes",
                    "order": "desc",
                    "pagesize": PAGESIZE,
                    "page": page,
                    "site": "stackoverflow",
                    "filter": "withbody",
                }
                
                logger.info(f"  → Fetching page {page}/{PAGES_PER_TAG} for tag '{tag}'")
                response = requests.get(BASE_URL, params=params, timeout=10)
                response.raise_for_status()
                
                data = response.json()
                items = data.get("items", [])
                
                if not items:
                    logger.warning(f"    No items returned for tag '{tag}' page {page}")
                    break
                
                for item in items:
                    title = item.get("title", "").strip()
                    
                    # Skip duplicates
                    if title in seen_titles:
                        continue
                    
                    body_html = item.get("body", "").strip()
                    
                    try:
                        body_text = BeautifulSoup(body_html, "html.parser").get_text(separator=" ")
                    except Exception as e:
                        logger.debug(f"    HTML parsing error: {e}")
                        body_text = body_html
                    
                    full_text = f"{title} {body_text}".strip()
                    
                    if len(full_text) >= MIN_TEXT_LENGTH:
                        posts_data.append({
                            "text": full_text,
                            "source": f"stackoverflow-{tag}",
                            "title": title,
                        })
                        seen_titles.add(title)
                
                time.sleep(1.5)
            
            except Exception as e:
                logger.warning(f"  Error fetching page {page} for tag '{tag}': {e}")
                continue
    
    logger.info(f"✓ Pass 1 complete: {len(posts_data)} posts scraped")
    
    # PASS 2: Targeted keywords for minority classes
    logger.info("PASS 2: Targeted scraping for minority classes")
    for class_label, keywords in CLASS_SPECIFIC_KEYWORDS.items():
        logger.info(f"Hunting for {class_label} using keywords: {keywords}")
        
        for keyword in keywords:
            try:
                params = {
                    "q": keyword,
                    "sort": "votes",
                    "order": "desc",
                    "pagesize": PAGESIZE,
                    "page": 1,
                    "site": "stackoverflow",
                    "filter": "withbody",
                }
                
                logger.info(f"  → Searching for keyword: '{keyword}'")
                response = requests.get(BASE_URL, params=params, timeout=10)
                response.raise_for_status()
                
                data = response.json()
                items = data.get("items", [])
                
                if not items:
                    logger.debug(f"    No results for keyword '{keyword}'")
                    continue
                
                for item in items:
                    title = item.get("title", "").strip()
                    
                    # Skip duplicates
                    if title in seen_titles:
                        continue
                    
                    body_html = item.get("body", "").strip()
                    
                    try:
                        body_text = BeautifulSoup(body_html, "html.parser").get_text(separator=" ")
                    except Exception as e:
                        logger.debug(f"    HTML parsing error: {e}")
                        body_text = body_html
                    
                    full_text = f"{title} {body_text}".strip()
                    
                    if len(full_text) >= MIN_TEXT_LENGTH:
                        posts_data.append({
                            "text": full_text,
                            "source": f"stackoverflow-keyword-{keyword}",
                            "title": title,
                        })
                        seen_titles.add(title)
                
                time.sleep(1.5)
            
            except Exception as e:
                logger.warning(f"  Error searching keyword '{keyword}': {e}")
                continue
    
    logger.info(f"✓ Pass 2 complete: {len(posts_data)} total posts after targeted scraping")
    
    if len(posts_data) == 0:
        logger.error("No posts scraped. Using mock data.")
        return _generate_mock_stackoverflow_data()
    
    return pd.DataFrame(posts_data)


def _generate_mock_stackoverflow_data() -> pd.DataFrame:
    """Generate mock Stack Overflow data for demonstration."""
    logger.info("Generating mock Stack Overflow dataset...")
    
    mock_texts = [
        "I updated my Express.js to version 4.18.0 to fix the critical vulnerability. Locked dependencies.",
        "Had to migrate from REST API v2 to GraphQL due to breaking changes in the new SDK.",
        "Applied a quick monkey patch to handle the auth issue temporarily until we can refactor.",
        "Complete refactor of our authentication system to use OAuth 2.0 instead of custom JWT.",
        "Pinned React to 17.0.2 to avoid compatibility issues with our legacy components.",
        "Migrating from deprecated Java 8 methods to Stream API for modern patterns.",
        "Quick fix: patched the vulnerable regex before production deployment.",
        "Full architectural redesign to support microservices and eliminate monolithic structure.",
        "Updated TypeScript version and fixed all type errors in the codebase.",
        "Monkey-patched Array.prototype to add missing polyfill for IE11 support.",
    ]
    
    expanded_texts = (mock_texts * 100)[:1000]
    
    df = pd.DataFrame({
        "text": expanded_texts,
        "source": np.random.choice(SO_TAGS, 1000),
        "title": [f"Post {i}" for i in range(1000)],
    })
    
    logger.info(f"✓ Generated {len(df)} mock Stack Overflow posts")
    return df


# ============================================================================
# PHASE 2: AUTO-ANNOTATION WITH DYNAMIC CLASS CAPPING
# ============================================================================
def auto_annotate_with_teacher(df: pd.DataFrame) -> pd.DataFrame:
    """
    Use BART zero-shot classifier with dynamic class capping.
    
    Once a class reaches CLASS_CAP_PER_CLASS, skip adding more examples of that class.
    For minority classes, dynamically search for related keywords to boost coverage.
    """
    logger.info("=" * 70)
    logger.info("PHASE 2: AUTO-ANNOTATION (TEACHER MODEL WITH DYNAMIC CLASS CAPPING)")
    logger.info("=" * 70)
    
    logger.info(f"Loading BART zero-shot classifier on {DEVICE}...")
    classifier = pipeline(
        "zero-shot-classification",
        model="facebook/bart-large-mnli",
        device=0 if torch.cuda.is_available() else -1,
    )
    logger.info("✓ BART model loaded")
    
    labels = INTENT_CLASSES
    predictions = []
    class_counts = {cls: 0 for cls in labels}
    skipped_due_to_cap = 0
    
    logger.info(f"Classifying {len(df)} texts with dynamic class capping...")
    for idx, text in enumerate(df["text"]):
        if idx % 100 == 0 and idx > 0:
            logger.info(f"  → Processed {idx}/{len(df)} texts. Counts: {class_counts}")
        
        try:
            truncated_text = text[:512]
            result = classifier(truncated_text, labels, multi_class=False)
            predicted_label = result["labels"][0]
            
            # Check if class has reached cap
            if class_counts[predicted_label] >= CLASS_CAP_PER_CLASS:
                skipped_due_to_cap += 1
                continue
            
            predictions.append(predicted_label)
            class_counts[predicted_label] += 1
        
        except Exception as e:
            logger.warning(f"Classification error for text {idx}: {e}")
            continue
    
    # Filter dataframe to only include predictions made
    df_filtered = df.iloc[:len(predictions)].copy()
    df_filtered["label"] = predictions
    
    logger.info(f"✓ Auto-annotation complete (skipped {skipped_due_to_cap} due to class cap)")
    logger.info("Final label distribution:")
    for label in labels:
        count = (df_filtered["label"] == label).sum()
        pct = 100 * count / len(df_filtered) if len(df_filtered) > 0 else 0
        logger.info(f"  {label}: {count} ({pct:.1f}%)")
    
    return df_filtered


# ============================================================================
# PHASE 3: STRICT CLASS IMBALANCE HANDLING
# ============================================================================
def handle_class_imbalance(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply strict class imbalance handling.
    
    Rule: Maximum difference between any two classes must be +/- 30 samples.
    Uses random oversampling for minority classes.
    """
    logger.info("=" * 70)
    logger.info("PHASE 3: STRICT CLASS IMBALANCE HANDLING")
    logger.info("=" * 70)
    
    # Count samples per class
    class_counts = df["label"].value_counts().to_dict()
    logger.info(f"Initial class distribution: {class_counts}")
    
    # Find min and max counts
    min_count = min(class_counts.values()) if class_counts else 0
    max_count = max(class_counts.values()) if class_counts else 0
    diff = max_count - min_count
    
    logger.info(f"Min count: {min_count}, Max count: {max_count}, Difference: {diff}")
    
    if diff > STRICT_MAX_DIFF:
        logger.warning(f"Class imbalance exceeds threshold ({diff} > {STRICT_MAX_DIFF}). Applying oversampling...")
        
        # Calculate target count for each class (slightly above max)
        target_count = max_count
        
        # Oversample minority classes to target
        for label in INTENT_CLASSES:
            label_df = df[df["label"] == label]
            current_count = len(label_df)
            
            if current_count < target_count:
                # Oversample by duplicating with noise
                shortage = target_count - current_count
                indices_to_duplicate = np.random.choice(label_df.index, size=shortage, replace=True)
                duplicated_df = df.loc[indices_to_duplicate].copy()
                df = pd.concat([df, duplicated_df], ignore_index=True)
                logger.info(f"  Oversampled {label}: {current_count} → {target_count - current_count} added")
    
    # Final verification
    final_counts = df["label"].value_counts().to_dict()
    final_diff = max(final_counts.values()) - min(final_counts.values())
    logger.info(f"✓ Final class distribution: {final_counts}")
    logger.info(f"✓ Final max difference: {final_diff}")
    
    return df


def save_and_split_dataset(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Save dataset to JSONL and split into train/eval/test.
    
    CRITICAL: Test set MUST be exactly TEST_SET_SIZE (180 examples).
    Master Document constraint — no flexibility.
    """
    logger.info(f"Saving dataset to {DATASET_PATH}...")
    
    with open(DATASET_PATH, "w") as f:
        for _, row in df.iterrows():
            json.dump({"text": row["text"], "label": row["label"]}, f)
            f.write("\n")
    
    logger.info(f"✓ Dataset saved ({len(df)} examples)")
    
    # Strict constraint: test set MUST be exactly TEST_SET_SIZE
    if len(df) < TEST_SET_SIZE:
        raise ValueError(
            f"Total dataset size ({len(df)}) is smaller than required test set size ({TEST_SET_SIZE}). "
            f"Cannot satisfy Master Document constraint."
        )
    
    test_size = TEST_SET_SIZE  # EXACTLY 180 — no min() logic
    remaining = len(df) - test_size
    train_size = int(remaining * (TRAIN_RATIO / (TRAIN_RATIO + EVAL_RATIO)))
    eval_size = remaining - train_size
    
    logger.info(f"Splitting dataset: train={train_size}, eval={eval_size}, test={test_size}")
    
    df = df.sample(frac=1, random_state=42).reset_index(drop=True)
    
    train_df = df[:train_size]
    eval_df = df[train_size:train_size + eval_size]
    test_df = df[train_size + eval_size:train_size + eval_size + test_size]
    
    # MASTER DOCUMENT CONSTRAINT ASSERTION
    assert len(test_df) == TEST_SET_SIZE, (
        f"Test set size failed to match Master Document constraint. "
        f"Expected {TEST_SET_SIZE}, got {len(test_df)}"
    )
    
    logger.info(f"✓ Split complete: train={len(train_df)}, eval={len(eval_df)}, test={len(test_df)}")
    logger.info(f"✓ Test set constraint verified: {len(test_df)} == {TEST_SET_SIZE}")
    
    return train_df, eval_df, test_df


# ============================================================================
# PHASE 4: LIGHTWEIGHT LEARNING RATE SEARCH
# ============================================================================
def find_best_learning_rate(
    train_dataset: Dataset,
    eval_dataset: Dataset,
) -> float:
    """
    Perform lightweight learning rate search.
    
    Tests LR_SEARCH_RATES for LR_SEARCH_EPOCHS epochs, returns best LR by validation F1.
    """
    logger.info("=" * 70)
    logger.info("PHASE 4: LIGHTWEIGHT LEARNING RATE SEARCH")
    logger.info("=" * 70)
    
    best_lr = LR_SEARCH_RATES[0]
    best_f1 = 0.0
    
    for lr in LR_SEARCH_RATES:
        logger.info(f"Testing learning rate: {lr}")
        
        model = DistilBertForSequenceClassification.from_pretrained(
            MODEL_NAME,
            num_labels=len(INTENT_CLASSES),
        )
        model.to(DEVICE)
        
        training_args = TrainingArguments(
            output_dir=f"./lr_search_{lr}",
            num_train_epochs=LR_SEARCH_EPOCHS,
            per_device_train_batch_size=BATCH_SIZE_TRAIN,
            per_device_eval_batch_size=BATCH_SIZE_EVAL,
            learning_rate=lr,
            warmup_steps=WARMUP_STEPS,
            weight_decay=WEIGHT_DECAY,
            eval_strategy=EVAL_STRATEGY,
            save_strategy="no",
            logging_steps=50,
            fp16=torch.cuda.is_available(),
            seed=42,
        )
        
        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=train_dataset,
            eval_dataset=eval_dataset,
            compute_metrics=compute_metrics,
        )
        
        trainer.train()
        
        # Evaluate on validation set
        eval_result = trainer.evaluate()
        val_f1 = eval_result.get("eval_f1_macro", 0.0)
        
        logger.info(f"  LR {lr}: Validation F1 = {val_f1:.4f}")
        
        if val_f1 > best_f1:
            best_f1 = val_f1
            best_lr = lr
        
        # Clean up
        shutil.rmtree(f"./lr_search_{lr}", ignore_errors=True)
    
    logger.info(f"✓ Best learning rate: {best_lr} (F1: {best_f1:.4f})")
    return best_lr


# ============================================================================
# PHASE 5: PREPARE DATASETS & CUSTOM TRAINER
# ============================================================================
def prepare_datasets(
    train_df: pd.DataFrame,
    eval_df: pd.DataFrame,
    test_df: pd.DataFrame,
) -> Tuple[Dataset, Dataset, Dataset]:
    """Convert pandas DataFrames to Hugging Face Datasets and tokenize."""
    logger.info("Loading tokenizer: {MODEL_NAME}")
    tokenizer = DistilBertTokenizer.from_pretrained(MODEL_NAME)
    logger.info("✓ Tokenizer loaded")
    
    def tokenize_function(examples):
        return tokenizer(
            examples["text"],
            padding="max_length",
            truncation=True,
            max_length=128,
        )
    
    # Convert to HF Datasets
    logger.info("Converting DataFrames to Hugging Face Datasets...")
    train_dataset = Dataset.from_pandas(train_df[["text", "label"]])
    eval_dataset = Dataset.from_pandas(eval_df[["text", "label"]])
    test_dataset = Dataset.from_pandas(test_df[["text", "label"]])
    
    # Map labels to IDs
    def map_label_to_id(example):
        example["label"] = LABEL_TO_ID[example["label"]]
        return example
    
    train_dataset = train_dataset.map(map_label_to_id)
    eval_dataset = eval_dataset.map(map_label_to_id)
    test_dataset = test_dataset.map(map_label_to_id)
    
    # Tokenize
    logger.info("Tokenizing datasets...")
    train_dataset = train_dataset.map(tokenize_function, batched=True)
    eval_dataset = eval_dataset.map(tokenize_function, batched=True)
    test_dataset = test_dataset.map(tokenize_function, batched=True)
    
    # Set format for PyTorch
    train_dataset.set_format("torch", columns=["input_ids", "attention_mask", "label"])
    eval_dataset.set_format("torch", columns=["input_ids", "attention_mask", "label"])
    test_dataset.set_format("torch", columns=["input_ids", "attention_mask", "label"])
    
    logger.info(f"✓ Datasets prepared")
    logger.info(f"  Train: {len(train_dataset)} examples")
    logger.info(f"  Eval:  {len(eval_dataset)} examples")
    logger.info(f"  Test:  {len(test_dataset)} examples")
    
    return train_dataset, eval_dataset, test_dataset


def compute_metrics(eval_pred):
    """Compute accuracy and macro F1 score."""
    predictions, labels = eval_pred
    predictions = np.argmax(predictions, axis=1)
    
    accuracy = accuracy_score(labels, predictions)
    macro_f1 = f1_score(labels, predictions, average="macro", zero_division=0)
    
    return {
        "accuracy": accuracy,
        "f1_macro": macro_f1,
    }


class WeightedTrainer(Trainer):
    """Custom Trainer with weighted cross-entropy loss."""
    
    def __init__(self, *args, class_weights: Optional[np.ndarray] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_weights = class_weights
    
    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels").to(DEVICE)
        outputs = model(**inputs)
        logits = outputs.logits
        
        # Compute weighted cross-entropy loss
        if self.class_weights is not None:
            weights = torch.tensor(self.class_weights, dtype=torch.float32, device=DEVICE)
            loss_fn = nn.CrossEntropyLoss(weight=weights)
        else:
            loss_fn = nn.CrossEntropyLoss()
        
        loss = loss_fn(logits, labels)
        
        return (loss, outputs) if return_outputs else loss


def calculate_class_weights(train_df: pd.DataFrame) -> np.ndarray:
    """Calculate weighted loss coefficients for each class."""
    class_counts = train_df["label"].value_counts().to_dict()
    total = len(train_df)
    
    weights = np.zeros(len(INTENT_CLASSES))
    for idx, label in enumerate(INTENT_CLASSES):
        count = class_counts.get(label, 1)
        weights[idx] = total / (len(INTENT_CLASSES) * count)
    
    weights = weights / weights.sum() * len(INTENT_CLASSES)  # Normalize
    logger.info(f"Class weights: {weights}")
    
    return weights


def fine_tune_model(
    train_dataset: Dataset,
    eval_dataset: Dataset,
    train_df: pd.DataFrame,
    best_lr: float,
) -> Tuple[DistilBertForSequenceClassification, DistilBertTokenizer]:
    """Fine-tune DistilBERT with weighted loss."""
    logger.info("=" * 70)
    logger.info("PHASE 5: FINE-TUNING (WITH WEIGHTED CROSS-ENTROPY LOSS)")
    logger.info("=" * 70)
    
    logger.info(f"Loading model: {MODEL_NAME}")
    model = DistilBertForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(INTENT_CLASSES),
    )
    model.to(DEVICE)
    logger.info("✓ Model loaded")
    
    # Calculate class weights
    class_weights = calculate_class_weights(train_df)
    
    logger.info("Setting up training arguments...")
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=NUM_EPOCHS,
        per_device_train_batch_size=BATCH_SIZE_TRAIN,
        per_device_eval_batch_size=BATCH_SIZE_EVAL,
        learning_rate=best_lr,
        warmup_steps=WARMUP_STEPS,
        weight_decay=WEIGHT_DECAY,
        eval_strategy=EVAL_STRATEGY,
        save_strategy=SAVE_STRATEGY,
        load_best_model_at_end=True,
        metric_for_best_model="f1_macro",
        greater_is_better=True,
        logging_dir="./logs",
        logging_steps=50,
        fp16=torch.cuda.is_available(),
        seed=42,
    )
    logger.info("✓ Training arguments configured")
    
    logger.info("Initializing Weighted Trainer...")
    trainer = WeightedTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        compute_metrics=compute_metrics,
        class_weights=class_weights,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
    )
    logger.info("✓ Trainer initialized with weighted loss")
    
    logger.info("Starting training...")
    trainer.train()
    logger.info("✓ Training complete")
    
    tokenizer = DistilBertTokenizer.from_pretrained(MODEL_NAME)
    
    return model, tokenizer


# ============================================================================
# PHASE 6: EVALUATION
# ============================================================================
def evaluate_model(
    model: DistilBertForSequenceClassification,
    tokenizer: DistilBertTokenizer,
    test_dataset: Dataset,
) -> Dict[str, Any]:
    """Evaluate fine-tuned model on test set."""
    logger.info("=" * 70)
    logger.info("PHASE 6: EVALUATION (MEETING THE TARGETS)")
    logger.info("=" * 70)
    
    model.eval()
    model.to(DEVICE)
    
    all_predictions = []
    all_labels = []
    
    logger.info(f"Evaluating on {len(test_dataset)} test examples...")
    with torch.no_grad():
        for batch in test_dataset:
            input_ids = batch["input_ids"].unsqueeze(0).to(DEVICE)
            attention_mask = batch["attention_mask"].unsqueeze(0).to(DEVICE)
            labels = batch["label"]
            
            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            logits = outputs.logits
            predictions = torch.argmax(logits, dim=-1)
            
            all_predictions.append(predictions.cpu().item())
            all_labels.append(labels.item())
    
    # Calculate metrics
    accuracy = accuracy_score(all_labels, all_predictions)
    macro_f1 = f1_score(all_labels, all_predictions, average="macro", zero_division=0)
    
    logger.info("\n" + "=" * 70)
    logger.info("TEST SET EVALUATION RESULTS")
    logger.info("=" * 70)
    logger.info(f"Accuracy: {accuracy:.4f}")
    logger.info(f"Macro F1:  {macro_f1:.4f}")
    
    # Print classification report with explicit labels to prevent crashes
    logger.info("\nClassification Report:")
    report = classification_report(
        all_labels,
        all_predictions,
        target_names=INTENT_CLASSES,
        labels=[0, 1, 2, 3],
        zero_division=0,
    )
    logger.info(report)
    
    # Check targets
    target_accuracy = 0.80
    target_f1 = 0.80
    
    if accuracy >= target_accuracy and macro_f1 >= target_f1:
        logger.info("\n" + "🎉 " * 10)
        logger.info(f"✓ SUCCESS! Targets met:")
        logger.info(f"  ✓ Accuracy {accuracy:.4f} >= {target_accuracy} ✓")
        logger.info(f"  ✓ Macro F1 {macro_f1:.4f} >= {target_f1} ✓")
        logger.info("🎉 " * 10)
    else:
        logger.warning(f"\n⚠️  Targets not fully met:")
        if accuracy < target_accuracy:
            logger.warning(f"  Accuracy {accuracy:.4f} < {target_accuracy}")
        if macro_f1 < target_f1:
            logger.warning(f"  Macro F1 {macro_f1:.4f} < {target_f1}")
    
    # Generate confusion matrix
    logger.info(f"Generating confusion matrix...")
    cm = confusion_matrix(all_labels, all_predictions)
    
    plt.figure(figsize=(8, 6))
    sns.heatmap(
        cm,
        annot=True,
        fmt="d",
        cmap="Blues",
        xticklabels=INTENT_CLASSES,
        yticklabels=INTENT_CLASSES,
    )
    plt.title("Confusion Matrix - Intent Classification (Test Set)")
    plt.ylabel("True Label")
    plt.xlabel("Predicted Label")
    plt.tight_layout()
    plt.savefig(CONFUSION_MATRIX_PATH, dpi=100)
    logger.info(f"✓ Confusion matrix saved to {CONFUSION_MATRIX_PATH}")
    plt.close()
    
    return {
        "accuracy": accuracy,
        "f1_macro": macro_f1,
        "confusion_matrix": cm,
        "predictions": all_predictions,
        "labels": all_labels,
    }


# ============================================================================
# PHASE 7: EXPORT & PACKAGING
# ============================================================================
def export_and_package_model(
    model: DistilBertForSequenceClassification,
    tokenizer: DistilBertTokenizer,
):
    """Save model, tokenizer, and config to an output directory and zip it."""
    logger.info("=" * 70)
    logger.info("PHASE 7: EXPORT & PACKAGING")
    logger.info("=" * 70)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    logger.info(f"Saving model to {OUTPUT_DIR}...")
    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    logger.info("✓ Model and tokenizer saved")
    
    # Save label mappings
    label_config = {
        "label_to_id": LABEL_TO_ID,
        "id_to_label": ID_TO_LABEL,
        "intent_classes": INTENT_CLASSES,
        "model_name": MODEL_NAME,
        "training_date": datetime.now().isoformat(),
    }
    
    label_config_path = os.path.join(OUTPUT_DIR, "label_config.json")
    with open(label_config_path, "w") as f:
        json.dump(label_config, f, indent=2)
    logger.info(f"✓ Label config saved to {label_config_path}")
    
    # Create README
    readme_path = os.path.join(OUTPUT_DIR, "README.md")
    readme_content = f"""# Sentinel-D DistilBERT Intent Classifier v1

## Model Details
- **Base Model**: {MODEL_NAME}
- **Task**: Sequence Classification (4 classes)
- **Training Date**: {datetime.now().isoformat()}
- **Classes**: {', '.join(INTENT_CLASSES)}

## Intent Classes
1. **VERSION_PIN**: Pinning or locking dependency versions
2. **API_MIGRATION**: Migrating between API versions or protocols
3. **MONKEY_PATCH**: Quick/temporary fixes via runtime patching
4. **FULL_REFACTOR**: Complete rewriting or structural redesign

## Usage

```python
from transformers import DistilBertTokenizer, DistilBertForSequenceClassification
import torch
import json

tokenizer = DistilBertTokenizer.from_pretrained("./distilbert-intent-classifier-v1")
model = DistilBertForSequenceClassification.from_pretrained("./distilbert-intent-classifier-v1")

text = "I updated my package.json to lock the Express version to 4.18.0"
inputs = tokenizer(text, return_tensors="pt", truncation=True, padding=True)

with torch.no_grad():
    outputs = model(**inputs)
    logits = outputs.logits
    predicted_class_id = logits.argmax(-1).item()

# Map ID back to label
label_config = json.load(open("label_config.json"))
predicted_label = label_config["id_to_label"][str(predicted_class_id)]
print(f"Predicted Intent: {{predicted_label}}")
```

## Files
- `pytorch_model.bin`: Fine-tuned model weights
- `config.json`: Model configuration
- `vocab.txt`: Tokenizer vocabulary
- `label_config.json`: Intent class mappings
- `README.md`: This file

## Training Configuration
- Epochs: {NUM_EPOCHS}
- Batch Size: {BATCH_SIZE_TRAIN}
- Learning Rate: Dynamic tuning from {min(LR_SEARCH_RATES)} to {max(LR_SEARCH_RATES)}
- Optimizer: AdamW with weighted cross-entropy loss
- Class Imbalance Handling: Random oversampling + weighted loss

## Performance Targets
- Accuracy: >= 0.80
- Macro F1: >= 0.80
"""
    
    with open(readme_path, "w") as f:
        f.write(readme_content)
    logger.info(f"✓ README saved to {readme_path}")
    
    # Zip the model directory
    zip_path = f"{OUTPUT_DIR}.zip"
    logger.info(f"Creating archive: {zip_path}...")
    
    shutil.make_archive(
        base_name=OUTPUT_DIR,
        format="zip",
        root_dir=".",
        base_dir=OUTPUT_DIR,
    )
    logger.info(f"✓ Model packaged to {zip_path}")
    
    logger.info("\n" + "=" * 70)
    logger.info("DOWNLOAD INSTRUCTIONS")
    logger.info("=" * 70)
    logger.info(f"Model directory: {OUTPUT_DIR}/")
    logger.info(f"Compressed archive: {zip_path}")
    logger.info("Download the .zip file from Kaggle and extract locally.")
    logger.info("=" * 70 + "\n")


# ============================================================================
# MAIN EXECUTION PIPELINE
# ============================================================================
def main():
    """Execute the complete fine-tuning pipeline."""
    logger.info("\n" + "🚀 " * 35)
    logger.info("SENTINEL-D DISTILBERT INTENT CLASSIFIER FINE-TUNING PIPELINE v2")
    logger.info("🚀 " * 35 + "\n")
    
    logger.info(f"Configuration Summary:")
    logger.info(f"  Model: {MODEL_NAME}")
    logger.info(f"  Device: {DEVICE}")
    logger.info(f"  Classes: {INTENT_CLASSES}")
    logger.info(f"  Class Cap: {CLASS_CAP_PER_CLASS} per class")
    logger.info(f"  Max Imbalance Diff: {STRICT_MAX_DIFF}")
    logger.info(f"  Train Epochs: {NUM_EPOCHS}")
    logger.info(f"  Batch Size: {BATCH_SIZE_TRAIN}")
    logger.info(f"  LR Search Rates: {LR_SEARCH_RATES}")
    logger.info("")
    
    try:
        # Phase 1: Scrape Stack Overflow
        stackoverflow_df = scrape_stackoverflow_posts()
        
        # Phase 2: Auto-annotate with BART
        annotated_df = auto_annotate_with_teacher(stackoverflow_df)
        
        # Phase 3: Handle class imbalance
        balanced_df = handle_class_imbalance(annotated_df)
        
        # Split dataset
        train_df, eval_df, test_df = save_and_split_dataset(balanced_df)
        
        # Prepare datasets
        train_dataset, eval_dataset, test_dataset = prepare_datasets(
            train_df, eval_df, test_df
        )
        
        # Phase 4: Lightweight LR search
        best_lr = find_best_learning_rate(train_dataset, eval_dataset)
        
        # Phase 5: Fine-tune with weighted loss
        model, tokenizer = fine_tune_model(train_dataset, eval_dataset, train_df, best_lr)
        
        # Phase 6: Evaluate
        eval_results = evaluate_model(model, tokenizer, test_dataset)
        
        # Phase 7: Export & Package
        export_and_package_model(model, tokenizer)
        
        logger.info("✓ Pipeline complete!")
        logger.info(f"Final Accuracy: {eval_results['accuracy']:.4f}")
        logger.info(f"Final Macro F1: {eval_results['f1_macro']:.4f}")
        
    except Exception as e:
        logger.error(f"❌ Pipeline failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()