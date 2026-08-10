# Assumes an existing VPC, private subnets, container registry and IAM instance profile.
variable "private_subnet_ids" { type = list(string) }
variable "gpu_worker_ami_id" { type = string }
variable "gpu_worker_instance_profile" { type = string }
variable "gpu_worker_security_group_id" { type = string }
variable "gpu_worker_image" { type = string }

resource "aws_sqs_queue" "render" {
  name                       = "ai-video-render"
  visibility_timeout_seconds = 7200 # Must exceed the maximum FFmpeg render timeout.
  message_retention_seconds  = 345600
}

resource "aws_launch_template" "render_gpu" {
  name_prefix   = "ai-video-render-gpu-"
  image_id      = var.gpu_worker_ami_id # GPU-ready AMI with NVIDIA Container Toolkit.
  instance_type = "g4dn.xlarge"

  iam_instance_profile { name = var.gpu_worker_instance_profile }
  network_interfaces {
    associate_public_ip_address = false
    security_groups             = [var.gpu_worker_security_group_id]
  }
  user_data = base64encode(<<-EOT
    #!/bin/bash
    set -euo pipefail
    docker run --restart=always --gpus all \
      -e CELERY_BROKER_URL='sqs://' \
      -e CELERY_RENDER_QUEUE='render' \
      ${var.gpu_worker_image}
  EOT
  )
}

resource "aws_autoscaling_group" "render_gpu" {
  name                = "ai-video-render-gpu"
  min_size            = 0
  max_size            = 20
  desired_capacity    = 0
  vpc_zone_identifier = var.private_subnet_ids
  launch_template { id = aws_launch_template.render_gpu.id, version = "$Latest" }
}

resource "aws_autoscaling_policy" "render_queue_scale_out" {
  name                   = "render-queue-over-ten"
  autoscaling_group_name = aws_autoscaling_group.render_gpu.name
  policy_type            = "SimpleScaling"
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = 1
  cooldown               = 180
}

resource "aws_cloudwatch_metric_alarm" "render_queue_over_ten" {
  alarm_name          = "ai-video-render-queue-over-ten"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 10
  dimensions = { QueueName = aws_sqs_queue.render.name }
  alarm_actions = [aws_autoscaling_policy.render_queue_scale_out.arn]
}

resource "aws_autoscaling_policy" "render_queue_scale_in" {
  name                   = "render-queue-drained"
  autoscaling_group_name = aws_autoscaling_group.render_gpu.name
  policy_type            = "SimpleScaling"
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = -1
  cooldown               = 600
}

resource "aws_cloudwatch_metric_alarm" "render_queue_drained" {
  alarm_name          = "ai-video-render-queue-drained"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  comparison_operator = "LessThanOrEqualToThreshold"
  threshold           = 1
  dimensions = { QueueName = aws_sqs_queue.render.name }
  alarm_actions = [aws_autoscaling_policy.render_queue_scale_in.arn]
}
