import os
import random
import shutil

# paths
images_path = "combined_dataset/images"
labels_path = "combined_dataset/labels"

train_img = "combined_dataset/images/train"
val_img = "combined_dataset/images/val"

train_lbl = "combined_dataset/labels/train"
val_lbl = "combined_dataset/labels/val"

# create folders
os.makedirs(train_img, exist_ok=True)
os.makedirs(val_img, exist_ok=True)
os.makedirs(train_lbl, exist_ok=True)
os.makedirs(val_lbl, exist_ok=True)

# get all images
images = [f for f in os.listdir(images_path) if f.endswith((".jpg", ".jpeg", ".png"))]

random.shuffle(images)

split = int(0.8 * len(images))  # 80% train

train_files = images[:split]
val_files = images[split:]

def move(files, img_dest, lbl_dest):
    for f in files:
        img_src = os.path.join(images_path, f)
        lbl_src = os.path.join(labels_path, f.replace(".jpg", ".txt").replace(".jpeg", ".txt").replace(".png", ".txt"))

        shutil.move(img_src, os.path.join(img_dest, f))
        if os.path.exists(lbl_src):
            shutil.move(lbl_src, os.path.join(lbl_dest, os.path.basename(lbl_src)))

move(train_files, train_img, train_lbl)
move(val_files, val_img, val_lbl)

print("✅ Dataset split into train & val")