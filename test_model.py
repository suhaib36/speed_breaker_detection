from ultralytics import YOLO

model = YOLO("models/best.pt")

print("Classes:", model.names)

results = model("test.jpeg", conf=0.10, show=True)

for r in results:
    print("Number of boxes:", len(r.boxes))
    for box in r.boxes:
        cls_id = int(box.cls[0])
        print("Class:", model.names[cls_id], "Confidence:", float(box.conf[0]))