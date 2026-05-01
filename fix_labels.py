import os

folders = [
    "combined_dataset/labels/train",
    "combined_dataset/labels/val",
    
]

for folder in folders:
    for filename in os.listdir(folder):
        if not filename.endswith(".txt"):
            continue

        filepath = os.path.join(folder, filename)

        with open(filepath, "r") as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) == 5:
                parts[0] = "0"
                new_lines.append(" ".join(parts) + "\n")

        with open(filepath, "w") as f:
            f.writelines(new_lines)

print("All labels converted to class 0.")