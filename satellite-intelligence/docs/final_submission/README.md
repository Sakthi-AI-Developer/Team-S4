# Final submission package

- [Editable project report](Satellite_Vision_Project_Report.docx)
- [Editable PowerPoint presentation](Satellite_Vision_Hackathon_Presentation.pptx)
- [Presentation/demo script](Satellite_Vision_Demo_Script.md)
- [Architecture and workflow diagrams](Satellite_Vision_Architecture.md)
- [Regeneration helper](generate_submission.py)

The report and slides distinguish implementation, local software tests, mocked cloud checks, and production verification. The slide chart is populated from the actual local synthetic demo endpoint; it is labeled synthetic and is not a real satellite observation.

To regenerate the DOCX/PPTX, install the optional `python-docx` and `python-pptx` packages in the backend environment, then run the helper from `satellite-intelligence/backend`:

```powershell
python ..\docs\final_submission\generate_submission.py
```

No production credentials, remote endpoints, satellite-provider requests, or production user data are used by the generator.
