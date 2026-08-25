# EC2 배포 (사장됨)

> **2026-08-25 역할 전환**: EC2 인스턴스(i-0c7dac45358b3fafc)는 백엔드(Spring) 서버가 됐고, 추론은 Lambda 단독이다.
> 이 문서의 절차와 자동 배포 워크플로(deploy-ec2.yml)는 제거됐다. EC2에 추론을 다시 올릴 일이 생기면
> 이 문서의 이전 리비전(git log -- docs/ec2-deploy.md)을 참조한다. 추론 배포는 [lambda-deploy.md](lambda-deploy.md), 백엔드 배포는 backend 레포 docs/deploy.md.

